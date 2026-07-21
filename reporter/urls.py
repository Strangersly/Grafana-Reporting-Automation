from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("invite/<str:token>/", views.accept_invitation, name="accept_invitation"),
    path("dashboards/", views.dashboard_list, name="dashboard_list"),
    path("dashboards/new/", views.dashboard_edit, name="dashboard_create"),
    path("dashboards/<int:pk>/edit/", views.dashboard_edit, name="dashboard_edit"),
    path("dashboards/<int:pk>/delete/", views.dashboard_delete, name="dashboard_delete"),
    path("dashboards/bulk/", views.dashboard_bulk, name="dashboard_bulk"),
    path("dashboards/import/", views.dashboard_import, name="dashboard_import"),
    path("schedules/", views.schedule_list, name="schedule_list"),
    path("schedules/new/", views.schedule_edit, name="schedule_create"),
    path("schedules/<int:pk>/edit/", views.schedule_edit, name="schedule_edit"),
    path("schedules/<int:pk>/delete/", views.schedule_delete, name="schedule_delete"),
    path("runs/", views.run_list, name="run_list"),
    path("runs/new/", views.run_create, name="run_create"),
    path("runs/<uuid:pk>/", views.run_detail, name="run_detail"),
    path("runs/<uuid:pk>/status.json", views.run_status, name="run_status"),
    path("runs/<uuid:pk>/cancel/", views.run_cancel, name="run_cancel"),
    path("runs/<uuid:pk>/download.zip", views.run_download, name="run_download"),
    path("reports/", views.report_library, name="report_library"),
    path("artifacts/<int:pk>/image/", views.artifact_image, name="artifact_image"),
    path("artifacts/<int:pk>/delete/", views.artifact_delete, name="artifact_delete"),
    path("settings/grafana/", views.grafana_settings, name="grafana_settings"),
    path("users/", views.user_list, name="user_list"),
    path("users/invite/", views.invite_user, name="invite_user"),
    path("users/<int:pk>/toggle/", views.user_toggle, name="user_toggle"),
    path("users/<int:pk>/role/", views.user_role, name="user_role"),
    path("health/", views.health, name="health"),
    path("health/external/", views.external_health, name="external_health"),
]
