from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST

from .models import MAX_UPCOMING_PER_PATIENT, Appointment, Doctor
from .ratelimit import ratelimit

BOOKING_WINDOW_DAYS = 14


def day_label(day):
    """'Today', 'Tomorrow', or 'Wed 21 Oct', in the clinic's time zone."""
    today = timezone.localdate()
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return f"{day:%a} {day.day} {day:%b}"


def home(request):
    specialties = Doctor.objects.order_by("specialty").values_list("specialty", flat=True).distinct()
    selected = request.GET.get("specialty", "")
    doctors = Doctor.objects.filter(specialty=selected) if selected in specialties else Doctor.objects.all()
    rows = []
    for doctor in doctors:
        slot = doctor.next_free_slot(BOOKING_WINDOW_DAYS)
        local = timezone.localtime(slot) if slot else None
        rows.append({"doctor": doctor, "slot": slot, "label": day_label(local.date()) if local else None})
    soonest = min((r for r in rows if r["slot"]), key=lambda r: r["slot"], default=None)
    next_visit = None
    if request.user.is_authenticated:
        next_visit = request.user.appointments.filter(
            status=Appointment.Status.BOOKED, start__gt=timezone.now()
        ).select_related("doctor").first()
    return render(
        request,
        "clinic/home.html",
        {
            "rows": rows,
            "soonest": soonest,
            "specialties": specialties,
            "selected": selected,
            "next_visit": next_visit,
            "limit": MAX_UPCOMING_PER_PATIENT,
        },
    )


def booking_page(request, doctor, day, reason="", selected_start=None, status=200):
    dates = [timezone.localdate() + timedelta(days=i) for i in range(BOOKING_WINDOW_DAYS)]
    if day not in dates:
        day = dates[0]
    working_days = set(doctor.working_hours.values_list("weekday", flat=True))
    days = []
    for d in dates:
        free = len(doctor.available_slots(d)) if d.weekday() in working_days else None  # None = day off
        days.append({"date": d, "free": free})
    context = {
        "doctor": doctor,
        "day": day,
        "days": days,
        "slots": doctor.available_slots(day),
        "hours": doctor.working_hours.order_by("weekday", "start_time"),
        "reason": reason,
        "selected_start": selected_start,
    }
    return render(request, "clinic/doctor_detail.html", context, status=status)


def doctor_detail(request, pk):
    doctor = get_object_or_404(Doctor, pk=pk)
    try:
        day = parse_date(request.GET.get("date", ""))
        start = parse_datetime(request.GET.get("start", ""))  # Preselected from "Book" on the doctors list.
    except ValueError:  # Well-formed but impossible, e.g. 2026-02-30.
        day, start = None, None
    return booking_page(request, doctor, day, selected_start=start)


@ratelimit("booking", limit=10)
@require_POST
@login_required
def book(request, pk):
    doctor = get_object_or_404(Doctor, pk=pk)
    try:
        start = parse_datetime(request.POST.get("start", ""))
    except ValueError:
        start = None
    reason = request.POST.get("reason", "")
    if start is None or timezone.is_naive(start):
        messages.error(request, "Please pick a time from the list.")
        return booking_page(request, doctor, None, reason=reason, status=400)
    try:
        appointment = Appointment.book(doctor, request.user, start, reason)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        day = timezone.localtime(start).date()
        return booking_page(request, doctor, day, reason=reason, selected_start=start, status=400)
    messages.success(request, f"Booked with {doctor} on {timezone.localtime(appointment.start):%A %d %B at %H:%M}.")
    return redirect("my_appointments")


@login_required
def my_appointments(request):
    appointments = request.user.appointments.select_related("doctor")
    now = timezone.now()
    upcoming = list(appointments.filter(start__gt=now, status=Appointment.Status.BOOKED))
    return render(
        request,
        "clinic/my_appointments.html",
        {
            "upcoming": upcoming,
            "past": appointments.exclude(start__gt=now, status=Appointment.Status.BOOKED).order_by("-start")[:10],
            "limit": MAX_UPCOMING_PER_PATIENT,
            "used_percent": round(100 * len(upcoming) / MAX_UPCOMING_PER_PATIENT),
        },
    )


@ratelimit("booking", limit=10)
@require_POST
@login_required
def cancel(request, pk):
    # Filtering by patient means another user's appointment is a 404, not a permission leak.
    appointment = get_object_or_404(Appointment, pk=pk, patient=request.user)
    try:
        appointment.cancel()
        messages.success(request, "Appointment cancelled.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("my_appointments")


@ratelimit("signup", limit=5, period=60 * 60)
def signup(request):
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save())
        messages.success(request, "Welcome! Your account is ready.")
        return redirect("home")
    return render(request, "registration/signup.html", {"form": form})


login_view = ratelimit("login", limit=5)(LoginView.as_view())


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")
