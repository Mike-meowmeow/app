from django.contrib import admin

from .models import Department, RecruitmentType


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')


@admin.register(RecruitmentType)
class RecruitmentTypeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')
