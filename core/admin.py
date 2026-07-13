from django.contrib import admin
from .models import *
from django.contrib.auth.models import Group, User
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django import forms

@admin.register(BotConfig)
class BotConfigAdmin(admin.ModelAdmin):
    list_display = ('key', 'value','value_preview', 'updated_at')
    search_fields = ('key', 'value')
    list_editable = ('value',)

    def value_preview(self, obj):
        return obj.value[:120] if obj.value else '-'
    value_preview.short_description = 'Value'

@admin.register(Decision)
class DecisionAdmin(admin.ModelAdmin):
    list_display = ('id', 'question_preview', 'category', 'authority_level', 'action_type', 'active', 'created_at')
    list_filter = ('category', 'authority_level', 'action_type', 'active')
    search_fields = ('question', 'context', 'ideal_answer', 'reasoning')
    list_editable = ('active',)

    def question_preview(self, obj):
        return obj.question[:100] + '...' if len(obj.question) > 100 else obj.question
    question_preview.short_description = 'Question'

@admin.register(Behavior)
class BehaviorAdmin(admin.ModelAdmin):
    list_display = ('id', 'situation_preview', 'tone', 'active', 'created_at')
    list_filter = ('active',)
    search_fields = ('situation', 'tone', 'example_response')
    list_editable = ('active',)

    def situation_preview(self, obj):
        return obj.situation[:100] + '...' if len(obj.situation) > 100 else obj.situation
    situation_preview.short_description = 'Situation'

@admin.register(AuthorityRule)
class AuthorityRuleAdmin(admin.ModelAdmin):
    list_display = ('id', 'action_type', 'allowed', 'active', 'created_at')
    list_filter = ('allowed', 'active')
    search_fields = ('action_type', 'condition', 'fallback_behavior')
    list_editable = ('active',)

@admin.register(FlaggedQuestion)
class FlaggedQuestionAdmin(admin.ModelAdmin):
    list_display = ('id', 'question_preview', 'flag_reason', 'status', 'created_at')
    list_filter = ('status', 'flag_reason')
    search_fields = ('question', 'ai_response', 'admin_answer')
    readonly_fields = ('created_at', 'resolved_at')

    def question_preview(self, obj):
        return obj.question[:100] + '...' if len(obj.question) > 100 else obj.question
    question_preview.short_description = 'Question'

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('id', 'session_id', 'role', 'content_preview', 'created_at')
    list_filter = ('role',)
    search_fields = ('session_id', 'content')

    def content_preview(self, obj):
        return obj.content[:120] if obj.content else '-'
    content_preview.short_description = 'Content'


# Unregister default so we can customize
admin.site.unregister(Group)
admin.site.unregister(User)


class CustomGroupAdminForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = "__all__"
        widgets = {
            'permissions': forms.SelectMultiple(attrs={'size': '20', 'style': 'width: 60%;'})
        }


@admin.register(Group)
class CustomGroupAdmin(GroupAdmin):
    form = CustomGroupAdminForm
    list_display = ('name', 'user_count')
    search_fields = ('name',)
    filter_horizontal = ('permissions',)

    def user_count(self, obj):
        return obj.user_set.count()
    user_count.short_description = "Users"


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'group_list', 'is_active')
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'groups')
    list_editable = ('is_active',)

    def group_list(self, obj):
        return ", ".join(g.name for g in obj.groups.all()) or "-"
    group_list.short_description = "Groups"