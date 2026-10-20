from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import MAX_UPCOMING_PER_PATIENT, Appointment, Doctor, WorkingHours

MONDAY = date(2030, 1, 7)  # A Monday safely in the future.


def at(hour, minute=0, day=MONDAY):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class ClinicTestCase(TestCase):
    def setUp(self):
        cache.clear()  # Rate-limit counters live in the cache; don't leak them between tests.
        self.doctor = Doctor.objects.create(name="Sara Ali", specialty="Dermatology")
        WorkingHours.objects.create(
            doctor=self.doctor, weekday=WorkingHours.Weekday.MONDAY, start_time=time(9), end_time=time(17)
        )
        self.patient = get_user_model().objects.create_user("patient", password="pw-for-tests-only")
        self.other = get_user_model().objects.create_user("other", password="pw-for-tests-only")

    def book(self, start, patient=None):
        return Appointment.objects.create(doctor=self.doctor, patient=patient or self.patient, start=start)


class AppointmentModelTests(ClinicTestCase):
    def test_double_booking_is_rejected_by_database(self):
        self.book(at(10))
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.book(at(10))

    def test_cancelled_slot_can_be_rebooked(self):
        self.book(at(10)).cancel()
        self.book(at(10))

    def test_clean_accepts_slot_inside_working_hours(self):
        Appointment(doctor=self.doctor, patient=self.patient, start=at(16, 30), reason="Checkup").full_clean()

    def test_clean_rejects_slot_outside_working_hours(self):
        for start in [at(8, 30), at(17), at(16, 45)]:
            with self.subTest(start=start), self.assertRaises(ValidationError):
                Appointment(doctor=self.doctor, patient=self.patient, start=start).full_clean()

    def test_clean_rejects_day_off(self):
        with self.assertRaises(ValidationError):
            Appointment(doctor=self.doctor, patient=self.patient, start=at(10) + timedelta(days=1)).full_clean()

    def test_clean_rejects_past_booking(self):
        with self.assertRaises(ValidationError):
            Appointment(doctor=self.doctor, patient=self.patient, start=at(10, day=date(2020, 1, 6))).full_clean()

    def test_book_gives_friendly_error_for_taken_slot(self):
        self.book(at(10), patient=self.other)
        with self.assertRaisesMessage(ValidationError, "already booked"):
            Appointment.book(self.doctor, self.patient, at(10), "Rash")

    def test_book_limits_upcoming_appointments(self):
        for i in range(MAX_UPCOMING_PER_PATIENT):
            Appointment.book(self.doctor, self.patient, at(9) + timedelta(minutes=30 * i), "Checkup")
        with self.assertRaisesMessage(ValidationError, "at most"):
            Appointment.book(self.doctor, self.patient, at(15), "Checkup")

    def test_book_requires_a_reason(self):
        for reason in ["", "   \n"]:
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                Appointment.book(self.doctor, self.patient, at(10), reason)
        self.assertEqual(Appointment.book(self.doctor, self.patient, at(10), "  Rash  ").reason, "Rash")

    def test_reason_length_is_limited(self):
        with self.assertRaises(ValidationError):
            Appointment.book(self.doctor, self.patient, at(10), "x" * 501)

    def test_cancel_rejects_past_appointment(self):
        past = Appointment.objects.create(doctor=self.doctor, patient=self.patient, start=at(10, day=date(2020, 1, 6)))
        with self.assertRaises(ValidationError):
            past.cancel()


class AvailableSlotsTests(ClinicTestCase):
    def test_lists_every_slot_in_working_hours(self):
        slots = self.doctor.available_slots(MONDAY)
        self.assertEqual(len(slots), 16)  # 09:00-17:00 in 30-minute steps.
        self.assertEqual((slots[0], slots[-1]), (at(9), at(16, 30)))

    def test_excludes_booked_but_not_cancelled_slots(self):
        self.book(at(10))
        self.book(at(11)).cancel()
        slots = self.doctor.available_slots(MONDAY)
        self.assertNotIn(at(10), slots)
        self.assertIn(at(11), slots)

    def test_empty_on_day_off_and_in_the_past(self):
        self.assertEqual(self.doctor.available_slots(MONDAY + timedelta(days=1)), [])
        self.assertEqual(self.doctor.available_slots(date(2020, 1, 6)), [])


class WebViewTests(ClinicTestCase):
    def test_anonymous_booking_redirects_to_login(self):
        response = self.client.post(reverse("book", args=[self.doctor.pk]), {"start": at(10).isoformat()})
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('book', args=[self.doctor.pk])}")
        self.assertFalse(Appointment.objects.exists())

    def test_patient_can_book_and_cancel(self):
        self.client.force_login(self.patient)
        response = self.client.post(
            reverse("book", args=[self.doctor.pk]), {"start": at(10).isoformat(), "reason": "Itchy rash"}
        )
        self.assertRedirects(response, reverse("my_appointments"))
        appointment = Appointment.objects.get()
        self.assertEqual(appointment.reason, "Itchy rash")
        self.assertContains(self.client.get(reverse("my_appointments")), "Itchy rash")
        self.client.post(reverse("cancel", args=[appointment.pk]))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.CANCELLED)

    def test_garbage_start_time_is_rejected(self):
        self.client.force_login(self.patient)
        for value in ["", "not-a-date", "2030-01-07T10:00:00", "2030-13-45T10:00:00+00:00"]:
            with self.subTest(value=value):
                self.client.post(reverse("book", args=[self.doctor.pk]), {"start": value})
        self.assertFalse(Appointment.objects.exists())

    def test_failed_booking_keeps_the_typed_reason(self):
        tomorrow = timezone.localdate() + timedelta(days=1)  # Must fall inside the page's booking window.
        WorkingHours.objects.create(doctor=self.doctor, weekday=tomorrow.weekday(), start_time=time(9), end_time=time(17))
        self.book(at(10, day=tomorrow), patient=self.other)
        self.client.force_login(self.patient)
        response = self.client.post(
            reverse("book", args=[self.doctor.pk]), {"start": at(10, day=tomorrow).isoformat(), "reason": "Mole check <b>"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already booked", status_code=400)
        self.assertContains(response, "Mole check &lt;b&gt;", status_code=400)  # Kept, and HTML-escaped.

    def test_home_filters_by_specialty(self):
        Doctor.objects.create(name="Lina Nasser", specialty="Pediatrics")
        response = self.client.get(reverse("home"), {"specialty": "Pediatrics"})
        self.assertEqual([r["doctor"].name for r in response.context["rows"]], ["Lina Nasser"])
        unknown = self.client.get(reverse("home"), {"specialty": "Nope"})
        self.assertEqual(len(unknown.context["rows"]), 2)

    def test_home_shows_each_doctors_next_free_time(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        WorkingHours.objects.create(doctor=self.doctor, weekday=tomorrow.weekday(), start_time=time(9), end_time=time(10))
        self.book(at(9, day=tomorrow))  # The first slot is taken, so the next free one is 09:30.
        Doctor.objects.create(name="No Hours", specialty="Pediatrics")
        rows = {r["doctor"].name: r for r in self.client.get(reverse("home")).context["rows"]}
        self.assertEqual(rows["Sara Ali"]["slot"], at(9, 30, day=tomorrow))
        self.assertEqual(rows["Sara Ali"]["label"], "Tomorrow")
        self.assertIsNone(rows["No Hours"]["slot"])

    def test_doctor_page_counts_free_times_per_day(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        WorkingHours.objects.filter(doctor=self.doctor).delete()
        WorkingHours.objects.create(doctor=self.doctor, weekday=tomorrow.weekday(), start_time=time(9), end_time=time(10))
        self.book(at(9, day=tomorrow))
        days = {d["date"]: d["free"] for d in self.client.get(reverse("doctor", args=[self.doctor.pk])).context["days"]}
        self.assertEqual(days[tomorrow], 1)
        self.assertIsNone(days[tomorrow + timedelta(days=1)])  # Not a working day.

    def test_cannot_cancel_someone_elses_appointment(self):
        appointment = self.book(at(10), patient=self.other)
        self.client.force_login(self.patient)
        response = self.client.post(reverse("cancel", args=[appointment.pk]))
        self.assertEqual(response.status_code, 404)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.BOOKED)

    def test_pages_render(self):
        self.client.force_login(self.patient)
        for url in [reverse("home"), reverse("doctor", args=[self.doctor.pk]), reverse("my_appointments"),
                    reverse("doctor", args=[self.doctor.pk]) + "?date=2026-02-30"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_healthz(self):
        self.assertContains(self.client.get(reverse("healthz")), "ok")

    def test_login_is_rate_limited(self):
        statuses = [
            self.client.post(reverse("login"), {"username": "patient", "password": "wrong"}).status_code
            for _ in range(6)
        ]
        self.assertEqual(statuses, [200] * 5 + [429])

    def test_signup_logs_in(self):
        response = self.client.post(
            reverse("signup"), {"username": "new", "password1": "a-Long-pass-123", "password2": "a-Long-pass-123"}
        )
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), get_user_model().objects.get(username="new").pk)


class ApiTests(ClinicTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()

    def test_doctors_and_slots_are_public(self):
        self.assertEqual(self.api.get(reverse("api_doctors")).json()[0]["name"], "Sara Ali")
        response = self.api.get(reverse("api_doctor_slots", args=[self.doctor.pk]), {"date": "2030-01-07"})
        self.assertEqual(len(response.json()["slots"]), 16)
        bad = self.api.get(reverse("api_doctor_slots", args=[self.doctor.pk]), {"date": "07/01/2030"})
        self.assertEqual(bad.status_code, 400)

    def test_appointments_require_login(self):
        self.assertIn(self.api.get(reverse("api_appointments")).status_code, [401, 403])

    def test_token_login_book_list_cancel(self):
        token = self.api.post(reverse("api_token"), {"username": "patient", "password": "pw-for-tests-only"}).json()
        self.api.credentials(HTTP_AUTHORIZATION=f"Token {token['token']}")
        booking = {"doctor": self.doctor.pk, "start": at(10).isoformat(), "reason": "Follow-up"}
        created = self.api.post(reverse("api_appointments"), booking)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(self.api.get(reverse("api_appointments")).json()[0]["reason"], "Follow-up")
        missing_reason = self.api.post(reverse("api_appointments"), {**booking, "start": at(11).isoformat(), "reason": ""})
        self.assertEqual(missing_reason.status_code, 400)
        taken = self.api.post(reverse("api_appointments"), booking)
        self.assertEqual(taken.status_code, 400)
        cancelled = self.api.post(reverse("api_cancel", args=[created.json()["id"]]))
        self.assertEqual(cancelled.json()["status"], "cancelled")

    def test_only_own_appointments_are_visible(self):
        self.book(at(10), patient=self.other)
        self.api.force_authenticate(self.patient)
        self.assertEqual(self.api.get(reverse("api_appointments")).json(), [])

    def test_booking_is_throttled(self):
        self.api.force_authenticate(self.patient)
        statuses = [
            self.api.post(reverse("api_appointments"), {"doctor": self.doctor.pk, "start": "bad"}).status_code
            for _ in range(11)
        ]
        self.assertEqual(statuses, [400] * 10 + [429])
