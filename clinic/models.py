import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import IntegrityError, models, transaction
from django.db.models import F, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# shortcut: every appointment is 30 minutes; move to a per-service duration if visit types differ.
SLOT_MINUTES = 30
# Stops one account from hoarding a doctor's calendar.
MAX_UPCOMING_PER_PATIENT = 5


class Doctor(models.Model):
    name = models.CharField(max_length=100)
    specialty = models.CharField(max_length=100)
    bio = models.TextField(blank=True, help_text="What the doctor treats, shown to patients.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"Dr. {self.name} ({self.specialty})"

    @property
    def initials(self):
        return "".join(word[0] for word in self.name.split()[:2]).upper()

    @property
    def hue(self):
        # A stable identity tint per doctor; consecutive ids land about 97 degrees apart.
        return (self.pk * 97 + 20) % 360

    def next_free_slot(self, days=14):
        # shortcut: two queries per day searched; batch into one query if the clinic grows past ~20 doctors.
        today = timezone.localdate()
        for offset in range(days):
            slots = self.available_slots(today + timedelta(days=offset))
            if slots:
                return slots[0]
        return None

    def available_slots(self, day):
        """Free, future slot start times on `day`, in the clinic's time zone."""
        step = timedelta(minutes=SLOT_MINUTES)
        slots = []
        for hours in self.working_hours.filter(weekday=day.weekday()):
            start = timezone.make_aware(datetime.combine(day, hours.start_time))
            end = timezone.make_aware(datetime.combine(day, hours.end_time))
            while start + step <= end:
                slots.append(start)
                start += step
        taken = set(
            self.appointments.filter(status=Appointment.Status.BOOKED, start__in=slots).values_list("start", flat=True)
        )
        now = timezone.now()
        return sorted(s for s in slots if s > now and s not in taken)


class WorkingHours(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 0
        TUESDAY = 1
        WEDNESDAY = 2
        THURSDAY = 3
        FRIDAY = 4
        SATURDAY = 5
        SUNDAY = 6

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="working_hours")
    weekday = models.IntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        verbose_name_plural = "working hours"
        constraints = [
            models.CheckConstraint(condition=Q(end_time__gt=F("start_time")), name="hours_end_after_start"),
        ]

    def __str__(self):
        return f"{self.doctor} {self.get_weekday_display()} {self.start_time}-{self.end_time}"


class Appointment(models.Model):
    class Status(models.TextChoices):
        BOOKED = "booked"
        CANCELLED = "cancelled"

    doctor = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="appointments")
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="appointments")
    start = models.DateTimeField()
    # Health information: only the patient and staff may see it, and it is never logged.
    # TextField's max_length only sizes the form widget, so the validator enforces it.
    reason = models.TextField("reason for visit", max_length=500, validators=[MaxLengthValidator(500)])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BOOKED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start"]
        constraints = [
            # The database, not the app, guarantees no double booking, even under concurrent requests.
            models.UniqueConstraint(
                fields=["doctor", "start"],
                condition=~Q(status="cancelled"),
                name="one_active_appointment_per_slot",
                violation_error_message="That time is already booked. Please pick another slot.",
            ),
        ]

    @property
    def end(self):
        return self.start + timedelta(minutes=SLOT_MINUTES)

    @classmethod
    def book(cls, doctor, patient, start, reason):
        """Validate and save a booking. Raises ValidationError with a message safe to show the patient."""
        appointment = cls(doctor=doctor, patient=patient, start=start, reason=reason.strip())
        upcoming = patient.appointments.filter(status=cls.Status.BOOKED, start__gt=timezone.now()).count()
        if upcoming >= MAX_UPCOMING_PER_PATIENT:
            raise ValidationError(f"You can have at most {MAX_UPCOMING_PER_PATIENT} upcoming appointments.")
        appointment.full_clean()
        try:
            with transaction.atomic():
                appointment.save()
        except IntegrityError:
            # Another request took the slot between full_clean() and save().
            raise ValidationError("That time was just booked. Please pick another slot.")
        logger.info("appointment %s booked: doctor=%s patient=%s", appointment.pk, doctor.pk, patient.pk)
        return appointment

    def cancel(self):
        if self.status == self.Status.CANCELLED:
            raise ValidationError("This appointment is already cancelled.")
        if self.start <= timezone.now():
            raise ValidationError("Past appointments can't be cancelled.")
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status"])
        logger.info("appointment %s cancelled", self.pk)

    def clean(self):
        if not self.doctor_id or not self.start:
            return  # Missing fields are reported by field validation.
        # Only new bookings: staff must still be able to edit past appointments in the admin.
        if self._state.adding and self.start <= timezone.now():
            raise ValidationError({"start": "Appointments must be in the future."})
        local = timezone.localtime(self.start)
        if local.minute % SLOT_MINUTES or local.second or local.microsecond:
            raise ValidationError({"start": f"Appointments start on {SLOT_MINUTES}-minute boundaries."})
        local_end = local + timedelta(minutes=SLOT_MINUTES)
        fits = self.doctor.working_hours.filter(
            weekday=local.weekday(), start_time__lte=local.time(), end_time__gte=local_end.time()
        ).exists()
        if not fits or local_end.date() != local.date():
            raise ValidationError({"start": "The doctor doesn't work at that time."})

    def __str__(self):
        return f"{self.patient} with {self.doctor} at {self.start:%Y-%m-%d %H:%M}"
