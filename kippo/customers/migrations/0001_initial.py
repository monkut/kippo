import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Move KippoCustomer from the ``projects`` app to the ``customers`` app.

    The model state is created here while the physical table created by
    projects.0036 (``projects_kippocustomer``) is renamed to
    ``customers_kippocustomer``. No data is copied — only the table is renamed,
    so foreign-key constraints follow automatically in PostgreSQL.
    """

    initial = True

    dependencies = [
        ('accounts', '0014_add_project_assignment_member_soft_ceiling'),
        ('projects', '0036_kippocustomer_kippoproject_customer'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='KippoCustomer',
                    fields=[
                        ('created_datetime', models.DateTimeField(auto_now_add=True)),
                        ('updated_datetime', models.DateTimeField(auto_now=True)),
                        ('closed_datetime', models.DateTimeField(editable=False, null=True)),
                        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                        ('name', models.CharField(max_length=256, verbose_name='顧客名')),
                        ('email', models.EmailField(blank=True, default='', max_length=254, verbose_name='メールアドレス')),
                        ('phone', models.CharField(blank=True, default='', max_length=50, verbose_name='電話番号')),
                        ('website', models.URLField(blank=True, default='', verbose_name='ウェブサイト')),
                        ('document_url', models.URLField(blank=True, default='', help_text='Link to customer-related documents (folder, drive, wiki, etc.)', verbose_name='ドキュメントURL')),
                        ('notes', models.TextField(blank=True, default='', verbose_name='メモ')),
                        ('display_as_active', models.BooleanField(default=True, help_text='If False, hidden from default admin lists', verbose_name='Display as Active')),
                        ('created_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_by', to=settings.AUTH_USER_MODEL)),
                        ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='accounts.kippoorganization', verbose_name='組織')),
                        ('updated_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_by', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'verbose_name': '顧客',
                        'verbose_name_plural': '顧客',
                        'unique_together': {('organization', 'name')},
                    },
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql='ALTER TABLE projects_kippocustomer RENAME TO customers_kippocustomer;',
                    reverse_sql='ALTER TABLE customers_kippocustomer RENAME TO projects_kippocustomer;',
                ),
            ],
        ),
    ]
