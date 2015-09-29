from django.contrib.auth.models import User
from rest_framework import serializers
from .models import Project, Release, Task, TaskComment


class UserSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = User
        fields = ('url', 'username', 'email', 'groups')


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