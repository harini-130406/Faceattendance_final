import django_filters
from django import forms
from .models import Attendence

class AttendenceFilter(django_filters.FilterSet):
    date = django_filters.DateFilter(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-modern form-control-sm'})
    )
    Student_ID = django_filters.CharFilter(
        lookup_expr='icontains',
        widget=forms.TextInput(attrs={'placeholder': 'Student ID...', 'class': 'form-control form-control-modern form-control-sm'})
    )
    branch = django_filters.CharFilter(
        lookup_expr='iexact',
        widget=forms.TextInput(attrs={'placeholder': 'Branch (e.g. CSE)', 'class': 'form-control form-control-modern form-control-sm'})
    )
    year = django_filters.CharFilter(
        lookup_expr='exact',
        widget=forms.TextInput(attrs={'placeholder': 'Year (1-4)', 'class': 'form-control form-control-modern form-control-sm'})
    )
    section = django_filters.CharFilter(
        lookup_expr='iexact',
        widget=forms.TextInput(attrs={'placeholder': 'Sec (A/B/C)', 'class': 'form-control form-control-modern form-control-sm'})
    )
    period = django_filters.CharFilter(
        lookup_expr='exact',
        widget=forms.TextInput(attrs={'placeholder': 'Period (1-7)', 'class': 'form-control form-control-modern form-control-sm'})
    )
    status = django_filters.ChoiceFilter(
        choices=(('Present', 'Present'), ('Absent', 'Absent')),
        widget=forms.Select(attrs={'class': 'form-control form-control-modern form-control-sm'})
    )

    class Meta:
        model = Attendence
        fields = ['date', 'branch', 'year', 'section', 'period', 'status', 'Student_ID']