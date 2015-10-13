from django.contrib.auth.models import User
from rest_framework import viewsets, permissions
from .serializers import UserSerializer, ProjectSerializer, ReleaseSerializer, TaskSerializer, TaskCommentSerializer

from .models import Project, Release, Task, TaskComment


class UserViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows User to be viewed or edited.
    """
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)


class ProjectViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows Project to be viewed or edited.
    """
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)


class ReleaseViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows Release to be viewed or edited.
    """
    queryset = Release.objects.all()
    serializer_class = ReleaseSerializer
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)


class TaskViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows Task to be viewed or edited.
    """
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)


class TaskCommentViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows TaskComment to be viewed or edited.
    """
    queryset = TaskComment.objects.all()
    serializer_class = TaskCommentSerializer
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)
