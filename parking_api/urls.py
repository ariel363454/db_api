from django.urls import path
from . import views

urlpatterns = [
    path('api/parking_bounds/', views.get_parking_bounds, name='parking_bounds'),
]