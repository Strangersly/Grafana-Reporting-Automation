from django.db import migrations, models


OLD_SELECTOR = ".panel-container, [data-testid='panel-container'], [data-testid='data-testid Panel header']"
NEW_SELECTOR = ".panel-container, [data-testid='panel-container'], [data-testid='data-testid panel content'], [data-testid^='data-testid Panel header ']"


def update_existing_selector(apps, schema_editor):
    connection_model = apps.get_model("reporter", "GrafanaConnection")
    connection_model.objects.filter(wait_for_selector=OLD_SELECTOR).update(wait_for_selector=NEW_SELECTOR)


def restore_existing_selector(apps, schema_editor):
    connection_model = apps.get_model("reporter", "GrafanaConnection")
    connection_model.objects.filter(wait_for_selector=NEW_SELECTOR).update(wait_for_selector=OLD_SELECTOR)


class Migration(migrations.Migration):
    dependencies = [("reporter", "0002_alter_schedule_weekly_day")]

    operations = [
        migrations.AlterField(
            model_name="grafanaconnection",
            name="wait_for_selector",
            field=models.CharField(blank=True, default=NEW_SELECTOR, max_length=500),
        ),
        migrations.RunPython(update_existing_selector, restore_existing_selector),
    ]
