from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics, serializers
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from .models import Appointment, Doctor


class BookingThrottle(UserRateThrottle):
    scope = "booking"


class DoctorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Doctor
        fields = ["id", "name", "specialty", "bio"]


class AppointmentSerializer(serializers.ModelSerializer):
    doctor = serializers.PrimaryKeyRelatedField(queryset=Doctor.objects.all())

    class Meta:
        model = Appointment
        fields = ["id", "doctor", "start", "end", "reason", "status"]
        read_only_fields = ["end", "status"]
        validators = []  # Appointment.book() validates, including the double-booking constraint.

    def create(self, validated_data):
        try:
            return Appointment.book(
                validated_data["doctor"], self.context["request"].user, validated_data["start"], validated_data["reason"]
            )
        except ValidationError as e:
            raise APIValidationError(e.messages)


class DoctorList(generics.ListAPIView):
    permission_classes = [AllowAny]
    queryset = Doctor.objects.all()
    serializer_class = DoctorSerializer


class DoctorSlots(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        doctor = get_object_or_404(Doctor, pk=pk)
        raw = request.query_params.get("date")
        try:
            day = parse_date(raw) if raw else timezone.localdate()
        except ValueError:
            day = None
        if day is None:
            raise APIValidationError({"date": "Use the format YYYY-MM-DD."})
        return Response({"doctor": doctor.pk, "date": day, "slots": doctor.available_slots(day)})


class AppointmentList(generics.ListCreateAPIView):
    serializer_class = AppointmentSerializer

    def get_queryset(self):
        return self.request.user.appointments.all()

    def get_throttles(self):
        throttles = super().get_throttles()
        if self.request.method == "POST":
            throttles.append(BookingThrottle())
        return throttles


class CancelAppointment(APIView):
    throttle_classes = [*APIView.throttle_classes, BookingThrottle]

    def post(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk, patient=request.user)
        try:
            appointment.cancel()
        except ValidationError as e:
            raise APIValidationError(e.messages)
        return Response(AppointmentSerializer(appointment).data)


class ObtainToken(ObtainAuthToken):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
