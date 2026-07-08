from rest_framework import serializers
from .models import Decision, Behavior, AuthorityRule, FlaggedQuestion

class DecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Decision
        fields = '__all__'

class BehaviorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Behavior
        fields = '__all__'

class AuthorityRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuthorityRule
        fields = '__all__'

class FlaggedQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlaggedQuestion
        fields = '__all__'