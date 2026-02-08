# Category Management — dynamic categories replacing hardcoded choices
# SQLite-compatible: drop index on (category, status) before removing the category column.

from django.db import migrations, models
import django.db.models.deletion


def create_default_categories_and_migrate(apps, schema_editor):
    """Create default categories and map existing AdRequest rows to them."""
    Category = apps.get_model('core', 'Category')
    AdRequest = apps.get_model('core', 'AdRequest')

    defaults = [
        ('Job', 'job_vacancy', '#00E676', 1),
        ('Rent', 'rent', '#00E5FF', 2),
        ('Events', 'events', '#FFC107', 3),
        ('Services', 'services', '#7C4DFF', 4),
        ('Sale', 'sale', '#8B949E', 5),
        ('Other', 'other', '#F0F6FC', 6),
    ]
    slug_to_category = {}
    for name, slug, color, order in defaults:
        cat, _ = Category.objects.get_or_create(
            slug=slug,
            defaults={'name': name, 'color': color, 'order': order, 'is_active': True},
        )
        slug_to_category[slug] = cat

    # Map existing AdRequests by old category CharField value (still present before RemoveField)
    for ad in AdRequest.objects.all():
        old_val = getattr(ad, 'category', None)
        if isinstance(old_val, str) and old_val:
            cat = slug_to_category.get(old_val) or slug_to_category.get('other')
        else:
            cat = slug_to_category.get('other')
        ad.category_new = cat
        ad.save(update_fields=['category_new'])


def reverse_migrate(apps, schema_editor):
    """Reverse: no safe rollback; category CharField would need to be re-added empty."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_iraniu_official_ads_bot'),
    ]

    operations = [
        # Step A: Remove index on (category, status) so SQLite can drop the column later.
        # SQLite does not allow dropping a column that is part of an index.
        migrations.RemoveIndex(
            model_name='adrequest',
            name='core_adrequ_categor_8516f7_idx',
        ),
        # Step B: Create the new Category model.
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Display name (e.g. Real Estate, Job)', max_length=64)),
                ('slug', models.SlugField(help_text='URL-safe identifier; used as callback_data in bot', max_length=64, unique=True)),
                ('color', models.CharField(default='#7C4DFF', help_text='Hex color for badges (e.g. #7C4DFF)', max_length=16)),
                ('icon', models.CharField(blank=True, help_text='Optional: Lucide/icon name', max_length=64)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('order', models.PositiveIntegerField(default=0, help_text='Sort order (lower first)')),
            ],
            options={
                'verbose_name': 'Category',
                'verbose_name_plural': 'Categories',
                'ordering': ['order', 'name'],
            },
        ),
        # Step C: Add new ForeignKey (nullable) for data migration.
        migrations.AddField(
            model_name='adrequest',
            name='category_new',
            field=models.ForeignKey(
                blank=True,
                help_text='Ad category (dynamic)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='ad_requests',
                to='core.category',
            ),
        ),
        # Step D: Data migration — copy old CharField category to category_new.
        migrations.RunPython(create_default_categories_and_migrate, reverse_migrate),
        # Step E: Drop the old category column (index already removed in Step A).
        migrations.RemoveField(
            model_name='adrequest',
            name='category',
        ),
        # Step F: Rename category_new to category.
        migrations.RenameField(
            model_name='adrequest',
            old_name='category_new',
            new_name='category',
        ),
        # Step G: Re-add composite index on (category, status). Same name so 0020 can rename if needed.
        migrations.AddIndex(
            model_name='adrequest',
            index=models.Index(fields=['category', 'status'], name='core_adrequ_categor_8516f7_idx'),
        ),
    ]
