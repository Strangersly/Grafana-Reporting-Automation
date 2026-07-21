from __future__ import annotations

from datetime import datetime

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import Dashboard, GrafanaConnection, Schedule, User


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True}))


class InvitationForm(forms.Form):
    email = forms.EmailField()
    role = forms.ChoiceField(choices=User.Role.choices)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email, is_active=True).exists():
            raise ValidationError("An active account already uses this email address.")
        return email


class InvitationAcceptForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    password1 = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password1") and cleaned.get("password1") != cleaned.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        if cleaned.get("password1"):
            password_validation.validate_password(cleaned["password1"])
        return cleaned


class GrafanaConnectionForm(forms.ModelForm):
    username = forms.CharField(required=False, help_text="Leave blank to keep the stored username.")
    password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the stored password.",
    )

    class Meta:
        model = GrafanaConnection
        exclude = ["username_ciphertext", "password_ciphertext"]
        widgets = {
            "wait_for_selector": forms.Textarea(attrs={"rows": 2}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get("username"):
            instance.set_username(self.cleaned_data["username"])
        if self.cleaned_data.get("password"):
            instance.set_password(self.cleaned_data["password"])
        if commit:
            instance.save()
        return instance


class DashboardForm(forms.ModelForm):
    class Meta:
        model = Dashboard
        fields = [
            "name",
            "site",
            "group",
            "category",
            "url",
            "query_params",
            "viewport_width",
            "viewport_height",
            "wait_for_selector",
            "enabled",
        ]
        widgets = {
            "url": forms.Textarea(attrs={"rows": 3}),
            "query_params": forms.Textarea(attrs={"rows": 3}),
            "wait_for_selector": forms.Textarea(attrs={"rows": 2}),
        }


class ScheduleForm(forms.ModelForm):
    weekly_day = forms.TypedChoiceField(choices=Schedule.WEEKDAY_CHOICES, coerce=int)

    class Meta:
        model = Schedule
        fields = [
            "name",
            "enabled",
            "recurrence",
            "preset",
            "monthly_day",
            "weekly_day",
            "run_time",
            "timezone",
            "all_dashboards",
            "dashboards",
        ]
        widgets = {
            "run_time": forms.TimeInput(attrs={"type": "time"}),
            "dashboards": forms.SelectMultiple(attrs={"size": 12}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["dashboards"].queryset = Dashboard.objects.filter(enabled=True)
        self.fields["dashboards"].required = False

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("all_dashboards") and not cleaned.get("dashboards"):
            self.add_error("dashboards", "Select at least one dashboard or enable all active dashboards.")
        return cleaned


class ManualRunForm(forms.Form):
    MODE_CHOICES = [
        ("all", "Monthly plus four weekly blocks"),
        ("monthly", "Monthly only"),
        ("weekly", "Four weekly blocks"),
        ("previous_7_days", "Previous 7 days"),
        ("custom", "Custom date range"),
    ]
    mode = forms.ChoiceField(choices=MODE_CHOICES)
    year = forms.IntegerField(min_value=2000, max_value=2200, initial=datetime.now().year, required=False)
    month = forms.IntegerField(min_value=1, max_value=12, initial=datetime.now().month, required=False)
    from_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    to_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    filename = forms.CharField(max_length=255, required=False, help_text="Optional for custom ranges, for example week1.png.")
    all_dashboards = forms.BooleanField(required=False, initial=True)
    dashboards = forms.ModelMultipleChoiceField(
        queryset=Dashboard.objects.none(), required=False, widget=forms.SelectMultiple(attrs={"size": 14})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["dashboards"].queryset = Dashboard.objects.filter(enabled=True)

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("mode")
        if mode in {"all", "monthly", "weekly"} and (not cleaned.get("year") or not cleaned.get("month")):
            self.add_error("year", "Year and month are required for this report type.")
        if mode == "custom":
            if not cleaned.get("from_date") or not cleaned.get("to_date"):
                self.add_error("from_date", "Custom reports require both dates.")
            elif cleaned["to_date"] < cleaned["from_date"]:
                self.add_error("to_date", "The end date cannot be before the start date.")
        if not cleaned.get("all_dashboards") and not cleaned.get("dashboards"):
            self.add_error("dashboards", "Select at least one dashboard or use all active dashboards.")
        return cleaned


class ImportUploadForm(forms.Form):
    file = forms.FileField(help_text="Upload .xlsx or .json.")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        suffix = upload.name.lower().rsplit(".", 1)[-1]
        if suffix not in {"xlsx", "json"}:
            raise ValidationError("Only .xlsx and .json files are supported.")
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError("Import files must be smaller than 5 MB.")
        return upload


class ImportMappingForm(forms.Form):
    name_column = forms.ChoiceField(label="Name column")
    url_column = forms.ChoiceField(label="URL column")
    site_column = forms.ChoiceField(label="Site column", required=False)
    group_column = forms.ChoiceField(label="Group column", required=False)
    category_column = forms.ChoiceField(label="Category column", required=False)

    def __init__(self, headers: list[str], *args, **kwargs):
        super().__init__(*args, **kwargs)
        required_choices = [(header, header) for header in headers]
        optional_choices = [("", "Not provided"), *required_choices]
        self.fields["name_column"].choices = required_choices
        self.fields["url_column"].choices = required_choices
        for field in ["site_column", "group_column", "category_column"]:
            self.fields[field].choices = optional_choices
        normalized = {header.lower().strip().replace(" ", "_"): header for header in headers}
        for field, candidates in {
            "name_column": ["name", "dashboard", "dashboard_name", "hostname"],
            "url_column": ["url", "link", "dashboard_url", "dashboard_link"],
            "site_column": ["site"],
            "group_column": ["group"],
            "category_column": ["category", "type"],
        }.items():
            for candidate in candidates:
                if candidate in normalized:
                    self.fields[field].initial = normalized[candidate]
                    break
