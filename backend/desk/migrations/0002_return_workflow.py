from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("desk", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="offsetsubmission",
            name="return_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="offsetsubmission",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "待复核"),
                    ("processing", "复核中"),
                    ("done", "已结清"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="ReturnHistory",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("reason", models.TextField()),
                ("return_count", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "returned_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="returns_made",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="return_history",
                        to="desk.offsetsubmission",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
