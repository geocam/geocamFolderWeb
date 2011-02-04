# __BEGIN_LICENSE__
# Copyright (C) 2008-2010 United States Government as represented by
# the Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
# __END_LICENSE__

from django.db import models
from django.contrib.auth.models import User, Group
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.utils.http import urlquote

from geocamUtil.models.UuidField import UuidField
from geocamUtil.models.ExtrasField import ExtrasField
from geocamUsers import settings

class Action(object):
    pass

class Actions(object):
    pass

# define constants drawn from settings.GEOCAM_USERS_ACTION_CHOICES
# example: Actions.VIEW = 0 from the entry (0, 'view')
for code, name in settings.GEOCAM_USERS_ACTION_CHOICES:
    setattr(Action, name.upper(), code)

# handy abbreviations for action combinations
Actions.READ = (Action.VIEW, Action.LIST)
Actions.WRITE = Actions.READ + (Action.INSERT, Action.DELETE, Action.CHANGE)
Actions.ALL = Actions.WRITE + (Action.ADMIN,)
Actions.NONE = ()

ACTION_LOOKUP = dict(settings.GEOCAM_USERS_ACTION_CHOICES)

# special groups defined in fixtures/initial_data.json
GROUP_ANYUSER_ID = 1
GROUP_AUTHUSER_ID = 2

def getCacheKey(resultFunc, args):
    prefix = '%s.%s.' % (resultFunc.__module__, resultFunc.__name__)
    return urlquote(prefix + '.'.join([repr(arg) for arg in args]))

def getWithCache(resultFunc, args, timeout):
    """
    Memoizes call to resultFunc(*args) using the Django cache.
    """
    cacheKey = getCacheKey(resultFunc, args)
    result = cache.get(cacheKey)
    if result is None:
        result = resultFunc(*args)
        cache.set(cacheKey, result, timeout)
    return result

def _addGroupAllowedFolders(allowed, groupId, action):
    #print '_addGroupAllowedFolders: groupId=%s action=%s' % (groupId, ACTION_LOOKUP[action])
    perms = GroupPermission.objects.filter(group__id=groupId, action=action).only('folder')
    for p in perms:
        allowed[p.folder.id] = p.folder
    #print '  _addGroupAllowedFolders: allowed=%s' % allowed.keys()

def _getLocalAllowedFolders(user, action):
    """
    Return folders for which @user has 'local' permission to perform
    @action, that is, without considering whether the folder is listable
    through all its ancestors.  Folders are returned as a dict of
    folder.id -> folder object.
    """
    #print '_getLocalAllowedFolders: user=%s action=%s' % (user, ACTION_LOOKUP[action])
    allowed = dict()

    _addGroupAllowedFolders(allowed, GROUP_ANYUSER_ID, action)

    if user is not None and user.is_active:
        _addGroupAllowedFolders(allowed, GROUP_AUTHUSER_ID, action)
        
        userPerms = UserPermission.objects.filter(user=user, action=action).only('folder')
        for p in userPerms:
            allowed[p.folder.id] = p.folder

        userGroups = user.groups.only('id')
        for g in userGroups:
            _addGroupAllowedFolders(allowed, g.id, action)

    #print '  _getLocalAllowedFolders: allowed=%s' % allowed.keys()
    return allowed

def _getListableFoldersNoCache(user):
    """
    Non-memoized version of _getListableFolders.
    """
    allowed = _getLocalAllowedFolders(user, Action.LIST)
    while 1:
        removeFolderIds = []
        for folder in allowed.itervalues():
            if folder.parent is not None and folder.parent not in allowed:
                removeFolderIds.append(folder.id)
        if removeFolderIds:
            for id in removeFolderIds:
                del allowed[id]
        else:
            break
    return allowed

def _getListableFolders(user):
    """
    Return folders for which @user has list access to both the folder
    and all its ancestors.  Folders are returned as a dict of folder.id
    -> folder object.
    """
    return getWithCache(_getListableFoldersNoCache, (user,),
                        settings.GEOCAM_USERS_PERMISSION_CACHE_TIMEOUT_SECONDS)

def _getAllowedFoldersNoCache(user, action):
    """
    Non-memoized version of getAllowedFolders.
    """
    localAllowed = _getLocalAllowedFolders(user, action)
    listableFolders = _getListableFolders(user)
    allowed = dict([(id, folder)
                    for id, folder in localAllowed.iteritems()
                    if folder.parent is None or folder.parent.id in listableFolders])
    return allowed

def getAllowedFolders(user, action):
    """
    Return folders for which @user has permission to perform @action.
    Folders are returned as a dict of folder.id -> folder object.
    """
    return getWithCache(_getAllowedFoldersNoCache, (user, action),
                        settings.GEOCAM_USERS_PERMISSION_CACHE_TIMEOUT_SECONDS)

def getAgentByName(agentString):
    if agentString.startswith('group:'):
        groupName = agentString[len('group:'):]
        return Group.objects.get(name=groupName)
    else:
        return User.objects.get(username=agentString)

def getActionListText(actions):
    actions.sort()
    return ''.join([ACTION_LOOKUP[action][0]
                    for action in actions])

class Folder(models.Model):
    name = models.CharField(max_length=32, db_index=True)
    parent = models.ForeignKey('self', null=True, db_index=True)
    notes = models.TextField(blank=True)
    uuid = UuidField(db_index=True)
    extras = ExtrasField()

    class Meta:
        app_label = 'geocamUsers'
        unique_together = ('name', 'parent')

    def isUserAllowed(self, user, action):
        return (((user is not None) and user.is_superuser)
                or (self.id in getAllowedFolders(user, action)))

    def _getAclDict(self):
        aclDict = {}
        for perm in UserPermission.objects.filter(folder=self):
            if perm.user.username not in aclDict:
                aclDict[perm.user.username] = []
            aclDict[perm.user.username].append(perm.action)
        for perm in GroupPermission.objects.filter(folder=self):
            agentName = 'group:' + perm.group.name
            if agentName not in aclDict:
                aclDict[agentName] = []
            aclDict[agentName].append(perm.action)
        return aclDict

    def getAclText(self):
        acl = self._getAclDict().items()
        acl.sort()
        for agentName, actions in acl:
            print '  %s %s' % (agentName, getActionListText(actions))

    def assertUserAllowed(self, user, action):
        if not self.isUserAllowed(user, action):
            if user is None:
                userName = '<anonymous>'
            else:
                userName = user.username
            raise PermissionDenied('user %s does not have %s permission for folder %s'
                                   % (userName, ACTION_LOOKUP[action], self.name))

    def addPermissionNoCheck(self, agent, action):
        if isinstance(agent, User):
            UserPermission(user=agent, action=action, folder=self).save()
        elif isinstance(agent, Group):
            GroupPermission(group=agent, action=action, folder=self).save()
        else:
            raise TypeError('expected User or Group')

    def removePermissionsNoCheck(self, agent):
        if isinstance(agent, User):
            UserPermission.objects.filter(user=agent, folder=self).delete()
        elif isinstance(agent, Group):
            GroupPermission.objects.filter(group=agent, folder=self).delete()
        else:
            raise TypeError('expected User or Group')

    def setPermissionsNoCheck(self, agent, actions):
        if isinstance(agent, str):
            agent = getAgentByName(agent)

        self.removePermissionsNoCheck(agent)
        for action in actions:
            self.addPermissionNoCheck(agent, action)

    def setPermissions(self, requestingUser, agent, actions):
        self.assertUserAllowed(requestingUser, Action.ADMIN)
        self.setPermissionsNoCheck(agent, actions)

    def clearAclNoCheck(self):
        UserPermission.objects.filter(folder=self).delete()
        GroupPermission.objects.filter(folder=self).delete()

    def copyAclNoCheck(self, folder):
        self.clearAclNoCheck()
        for perm in UserPermission.objects.filter(folder=folder):
            UserPermission(user=perm.user,
                           action=perm.action,
                           folder=self).save()
        for perm in GroupPermission.objects.filter(folder=folder):
            GroupPermission(group=perm.group,
                            action=perm.action,
                            folder=self).save()


    def mkdirNoCheck(self, name, admin=None):
        # note: db-level uniqueness check will fail if the subdir already exists
        subFolder = Folder(name=name, parent=self)
        subFolder.save()

        subFolder.copyAclNoCheck(self)
        if admin:
            subFolder.setPermissionsNoCheck(admin, Actions.ALL)
        
        return subFolder

    def mkdir(self, requestingUser, name):
        self.assertUserAllowed(requestingUser, Action.INSERT)
        return self.mkdirNoCheck(name, admin=requestingUser)

    @staticmethod
    def getRootFolder():
        return Folder.objects.get(pk=1)

class UserPermission(models.Model):
    user = models.ForeignKey(User, db_index=True)
    action = models.PositiveIntegerField(choices=settings.GEOCAM_USERS_ACTION_CHOICES, db_index=True)
    folder = models.ForeignKey(Folder, db_index=True)

    class Meta:
        app_label = 'geocamUsers'

class GroupPermission(models.Model):
    group = models.ForeignKey(Group, db_index=True)
    action = models.PositiveIntegerField(choices=settings.GEOCAM_USERS_ACTION_CHOICES, db_index=True)
    folder = models.ForeignKey(Folder, db_index=True)
    
    class Meta:
        app_label = 'geocamUsers'

class FolderMember(models.Model):
    """
    Mixin class for objects that are contained in a Folder and obey its
    access controls.
    """
    folder = models.ForeignKey(Folder)
    
    class Meta:
        app_label = 'geocamUsers'
        abstract = True

    @classmethod
    def allowedObjects(cls, requestingUser, action=Action.VIEW):
        if (requestingUser is not None) and requestingUser.is_superuser:
            return cls.objects
        else:
            allowedFolderIds = getAllowedFolders(requestingUser, action).iterkeys()
            return cls.objects.filter(folder__id__in=allowedFolderIds)

    def saveNoCheck(self, *args, **kwargs):
        super(FolderMember, self).save(*args, **kwargs)

    def save(self, *args, **kwargs):
        requestingUser = kwargs.pop('requestingUser', None)
        if self.pk is not None:
            self.folder.assertUserAllowed(requestingUser, Action.CHANGE)
        else:
            self.folder.assertUserAllowed(requestingUser, Action.INSERT)
        self.saveNoCheck(*args, **kwargs)

    def deleteNoCheck(self, *args, **kwargs):
        super(FolderMember, self).delete(*args, **kwargs)

    def delete(self, *args, **kwargs):
        requestingUser = kwargs.pop('requestingUser', None)
        self.folder.assertUserAllowed(requestingUser, Action.DELETE)
        self.deleteNoCheck(*args, **kwargs)

class FolderMemberExample(FolderMember):
    """
    This model exists only to support testing the FolderMember mixin.
    """
    name = models.CharField(max_length=32)
