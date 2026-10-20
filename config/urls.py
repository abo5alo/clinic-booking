from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from clinic import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/login/', views.login_view, name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('accounts/signup/', views.signup, name='signup'),
    path('', include('clinic.urls')),
]
