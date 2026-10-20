from django.contrib import admin

from .models import Appointment, Doctor, WorkingHours


class WorkingHoursInline(admin.TabularInline):
    model = WorkingHours
    extra = 1


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ["name", "specialty"]
    search_fields = ["name", "specialty"]
    inlines = [WorkingHoursInline]


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ["start", "doctor", "patient", "status"]
    list_filter = ["status", "doctor"]
    search_fields = ["patient__username", "doctor__name", "reason"]
    date_hierarchy = "start"
