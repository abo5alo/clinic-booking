import os
from datetime import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.utils import timezone

from clinic.models import Appointment, Doctor, WorkingHours

# (name, specialty, bio, working weekdays) -- sample data for the public demo only.
DOCTORS = [
    ("Sara Ali", "Dermatology",
     "Skin, hair and nail conditions: acne, eczema, psoriasis, rashes, moles and allergic reactions.", range(0, 5)),
    ("Omar Haddad", "General Practice",
     "Your first stop for everyday health: check-ups, colds and flu, minor injuries, prescriptions and referrals.",
     range(0, 6)),
    ("Lina Nasser", "Pediatrics",
     "Care for babies, children and teens: vaccinations, growth checks, fevers, asthma and childhood illnesses.",
     [0, 1, 3, 4, 5]),
    ("Yousef Karim", "Cardiology",
     "Heart and blood vessel care: chest pain, high blood pressure, palpitations, cholesterol and heart check-ups.",
     [1, 2, 3]),
]
DEMO_VISITS = [("Omar Haddad", "Annual check-up and blood pressure reading"),
               ("Sara Ali", "Itchy rash on my arm for two weeks")]


class Command(BaseCommand):
    help = "Fill an empty demo site with sample doctors, and a demo patient if DJANGO_DEMO_PASSWORD is set. Safe to rerun."

    def handle(self, *args, **options):
        if not settings.DEMO:
            self.stdout.write("DJANGO_DEMO is not 1; skipping demo data.")
            return

        for name, specialty, bio, weekdays in DOCTORS:
            doctor, created = Doctor.objects.get_or_create(name=name, defaults={"specialty": specialty, "bio": bio})
            if created:
                for day in weekdays:
                    WorkingHours.objects.create(doctor=doctor, weekday=day, start_time=time(9), end_time=time(17))
        self.stdout.write(f"{Doctor.objects.count()} doctors ready.")

        password = os.environ.get("DJANGO_DEMO_PASSWORD")
        if not password:
            self.stdout.write("DJANGO_DEMO_PASSWORD not set; no demo patient.")
            return
        demo, _ = get_user_model().objects.get_or_create(username="demo")
        demo.set_password(password)
        demo.save()

        # Keep the demo patient's "My visits" populated as old bookings pass.
        upcoming = demo.appointments.filter(status=Appointment.Status.BOOKED, start__gt=timezone.now()).count()
        for name, reason in DEMO_VISITS[upcoming:]:
            doctor = Doctor.objects.get(name=name)
            slot = doctor.next_free_slot()
            if slot:
                try:
                    Appointment.book(doctor, demo, slot, reason)
                except ValidationError:
                    pass  # Taken in the meantime, or at the limit; the demo works without it.
        self.stdout.write("Demo patient ready.")
