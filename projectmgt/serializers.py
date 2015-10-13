from django.contrib.auth.models import User
from rest_framework import serializers
from .models import Project, Release, Task, TaskComment


class UserSerializer(serializers.HyperlinkedModelSerializer):
    tasks = serializers.PrimaryKeyRelatedField(many=True,
                                               queryset=Task.objects.all())

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'tasks')


class ProjectSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Project


class ReleaseSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Release


class TaskSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Task


class TaskCommentSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = TaskComment