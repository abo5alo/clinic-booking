import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clinic', '0002_alter_doctor_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='doctor',
            name='bio',
            field=models.TextField(blank=True, help_text='What the doctor treats, shown to patients.'),
        ),
        migrations.AddField(
            model_name='appointment',
            name='reason',
            # Existing appointments get an empty reason; new ones must give one.
            field=models.TextField(
                default='',
                max_length=500,
                validators=[django.core.validators.MaxLengthValidator(500)],
                verbose_name='reason for visit',
            ),
            preserve_default=False,
        ),
    ]
