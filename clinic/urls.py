from django.urls import path

from . import api, views

urlpatterns = [
    path("", views.home, name="home"),
    path("doctors/<int:pk>/", views.doctor_detail, name="doctor"),
    path("doctors/<int:pk>/book/", views.book, name="book"),
    path("appointments/", views.my_appointments, name="my_appointments"),
    path("appointments/<int:pk>/cancel/", views.cancel, name="cancel"),
    path("healthz/", views.healthz, name="healthz"),
    path("api/doctors/", api.DoctorList.as_view(), name="api_doctors"),
    path("api/doctors/<int:pk>/slots/", api.DoctorSlots.as_view(), name="api_doctor_slots"),
    path("api/appointments/", api.AppointmentList.as_view(), name="api_appointments"),
    path("api/appointments/<int:pk>/cancel/", api.CancelAppointment.as_view(), name="api_cancel"),
    path("api/token/", api.ObtainToken.as_view(), name="api_token"),
]
