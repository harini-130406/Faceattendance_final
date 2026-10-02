import os
from django.apps import AppConfig


class AttendenceSysConfig(AppConfig):
    name = 'attendence_sys'
    path = os.path.dirname(os.path.abspath(__file__))
