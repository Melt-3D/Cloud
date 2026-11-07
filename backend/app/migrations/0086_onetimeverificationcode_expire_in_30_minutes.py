from django.db import migrations, models
import app.models.other_models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0085_printerprediction_normalized_p'),
    ]

    operations = [
        migrations.AlterField(
            model_name='onetimeverificationcode',
            name='expired_at',
            field=models.DateTimeField(db_index=True, default=app.models.other_models.thirty_minutes_later),
        ),
    ]
