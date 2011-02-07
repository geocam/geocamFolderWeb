# __BEGIN_LICENSE__
# Copyright (C) 2008-2010 United States Government as represented by
# the Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
# __END_LICENSE__

import os
from cStringIO import StringIO

from django.db import models
from django.contrib.auth.models import User, Group
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.utils.http import urlquote

from geocamUtil.models.UuidField import UuidField
from geocamUtil.models.ExtrasField import ExtrasField
from geocamUsers import settings

ACTION_CHOICES = (
    'view', # view members
    'list', # list subfolders
    'add', # add members
    'delete', # delete members
    'change', # change existing members
    'manage', # change access control list
    )
ACTION_LOOKUP = dict([name[0], name] for name in ACTION_CHOICES)

class Action(object):
    pass

# define constants drawn from ACTION_CHOICES
# example: Actions.VIEW = 'v' from the entry 'view'
for name in ACTION_CHOICES:
    setattr(Action, name.upper(), name[0])

# handy abbreviations for action combinations
class Actions(object):
    READ = 'vl'
    WRITE = 'vladc'
    ALL = 'vladcm'
    NONE = ''

# special groups defined in fixtures/initial_data.json
GROUP_ANYUSER_ID = 1
GROUP_AUTHUSER_ID = 2

FOLDER_CACHE_VERSION = 1

def getCacheKey(resultFunc, args):
    prefix = '%s.%s.%s.' % (FOLDER_CACHE_VERSION, resultFunc.__module__, resultFunc.__name__)
    return urlquote(prefix + '.'.join([repr(arg) for arg in args]))

def getWithCache(resultFunc, args, timeout):
    """
    Memoizes call to resultFunc(*args) using the Django cache.
    """
    if settings.GEOCAM_USERS_FOLDER_CACHE_ENABLED:
        cacheKey = getCacheKey(resultFunc, args)
        result = cache.get(cacheKey)
        if result is None:
            result = resultFunc(*args)
            cache.set(cacheKey, result, timeout)
        return result
    else:
        return resultFunc(*args)

def flushCache():
    global FOLDER_CACHE_VERSION
    FOLDER_CACHE_VERSION += 1

def _addGroupAllowedFolders(allowed, groupId, action):
    perms = GroupPermission.allowing(action).filter(group__id=groupId).only('folder')
    for p in perms:
        allowed[p.folder.id] = p.folder

def _getAllowedFoldersNoCache(user, action):
    """
    Non-memoized version of getAllowedFolders.
    """
    allowed = dict()

    _addGroupAllowedFolders(allowed, GROUP_ANYUSER_ID, action)

    if user is not None and user.is_active:
        _addGroupAllowedFolders(allowed, GROUP_AUTHUSER_ID, action)
        
        userPerms = UserPermission.allowing(action).filter(user=user).only('folder')
        for p in userPerms:
            allowed[p.folder.id] = p.folder

        userGroups = user.groups.only('id')
        for g in userGroups:
            _addGroupAllowedFolders(allowed, g.id, action)

    return allowed

def getAllowedFolders(user, action):
    """
    Return folders for which @user has permission to perform @action.
    Folders are returned as a dict of folder.id -> folder object.
    """
    return getWithCache(_getAllowedFoldersNoCache, (user, action),
                        settings.GEOCAM_USERS_FOLDER_CACHE_TIMEOUT_SECONDS)

class FolderTree(object):
    """
    A data structure that caches relationships in the Folder table.  The
    @root member of FolderTree is the root of a tree of folders; each
    folder has is annotated with a member @subFolders, which is a list
    of subfolders, and @path, which is the complete path to that folder.
    The @byId member of FolderTree is a lookup table id -> folder.
    """
    def __init__(self, root, byId):
        self.root = root
        self.byId = byId

def _getFolderTreeNoCache():
    """
    Non-memoized version of getFolderTree().
    """
    folders = Folder.objects.all().only('id', 'name', 'parent')
    subFolderLookup = {}
    for f in folders:
        subFolderLookup[f.parent_id] = subFolderLookup.get(f.parent_id, []) + [f]
    [root] = subFolderLookup[None]
    tree = FolderTree(root, dict([(f.id, f) for f in folders]))
    root.path = '/'
    queue = [root]
    while queue:
        current = queue.pop()
        current.subFolders = {}
        for subFolder in subFolderLookup.get(current.id, []):
            subFolder.path = current.path + '/' + subFolder.name
            current.subFolders[subFolder.name] = subFolder
            queue.append(subFolder)
    return tree
    
def getFolderTree():
    """
    Returns a tree data structure for all folders in the system.  See
    FolderTree class for details.
    """
    return getWithCache(_getFolderTreeNoCache, (),
                        settings.GEOCAM_USERS_FOLDER_CACHE_TIMEOUT_SECONDS)

def getAgentByName(agentString):
    if agentString.startswith('group:'):
        groupName = agentString[len('group:'):]
        return Group.objects.get(name=groupName)
    else:
        return User.objects.get(username=agentString)

class Folder(models.Model):
    name = models.CharField(max_length=32, db_index=True)
    parent = models.ForeignKey('self', null=True, db_index=True)
    notes = models.TextField(blank=True)
    uuid = UuidField(db_index=True)
    extras = ExtrasField()

    class Meta:
        app_label = 'geocamUsers'
        unique_together = ('name', 'parent')

    def __unicode__(self):
        result = self.name
        if self.parent is not None:
            result += ' parent=%s' % self.parent.name
        return result

    def save(self, *args, **kwargs):
        # folder change invalidates permission cache
        flushCache()
        super(Folder, self).save(*args, **kwargs)

    def isAllowed(self, user, action):
        return (not settings.GEOCAM_USERS_ACCESS_CONTROL_ENABLED
                or (((user is not None) and user.is_superuser)
                    or (self.id in getAllowedFolders(user, action))))

    def _getAclDict(self):
        aclDict = {}
        for perm in UserPermission.objects.filter(folder=self):
            aclDict[perm.user.username] = perm.getActions()
        for perm in GroupPermission.objects.filter(folder=self):
            agentName = 'group:' + perm.group.name
            aclDict[agentName] = perm.getActions()
        return aclDict

    def getAclText(self):
        acl = self._getAclDict().items()
        acl.sort()
        out = StringIO()
        for agentName, actions in acl:
            print >>out, '  %s %s' % (agentName, actions)
        return out.getvalue()

    def assertAllowed(self, user, action):
        if not self.isAllowed(user, action):
            if user is None:
                userName = '<anonymous>'
            else:
                userName = user.username
            raise PermissionDenied('user %s does not have %s permission for folder %s'
                                   % (userName, ACTION_LOOKUP[action], self.name))

    def setPermissionsNoCheck(self, agent, actions):
        if isinstance(agent, str):
            agent = getAgentByName(agent)

        if isinstance(agent, User):
            if actions == '':
                UserPermission.objects.filter(user=agent, folder=self).delete()
            else:
                perm, created = UserPermission.objects.get_or_create(user=agent, folder=self)
                perm.setActions(actions)
                perm.save()
        elif isinstance(agent, Group):
            if actions == '':
                GroupPermission.objects.filter(group=agent, folder=self).delete()
            else:
                perm, created = GroupPermission.objects.get_or_create(group=agent, folder=self)
                perm.setActions(actions)
                perm.save()
        else:
            raise TypeError('expected User, Group, or str')

    def setPermissions(self, requestingUser, agent, actions):
        self.assertAllowed(requestingUser, Action.MANAGE)
        self.setPermissionsNoCheck(agent, actions)

    def clearAclNoCheck(self):
        UserPermission.objects.filter(folder=self).delete()
        GroupPermission.objects.filter(folder=self).delete()

    def copyAclNoCheck(self, folder):
        self.clearAclNoCheck()
        for perm in UserPermission.objects.filter(folder=folder):
            newPerm = UserPermission(user=perm.user, folder=self)
            newPerm.setActions(perm.getActions())
            newPerm.save()
        for perm in GroupPermission.objects.filter(folder=folder):
            newPerm = GroupPermission(group=perm.group, folder=self)
            newPerm.setActions(perm.getActions())
            newPerm.save()

    def makeSubFolderNoCheck(self, name, admin=None):
        # note: db-level uniqueness check will fail if the subdir already exists
        subFolder = Folder(name=name, parent=self)
        subFolder.save()

        subFolder.copyAclNoCheck(self)
        if admin:
            subFolder.setPermissionsNoCheck(admin, Actions.ALL)
        
        return subFolder

    def makeSubFolder(self, requestingUser, name):
        self.assertAllowed(requestingUser, Action.ADD)
        return self.makeSubFolderNoCheck(name, admin=requestingUser)

    def removeSubFolderNoCheck(self, name):
        Folder.objects.get(name=name, parent=self).delete()

    def removeSubFolder(self, requestingUser, name):
        self.assertAllowed(requestingUser, Action.DELETE)
        return self.removeSubFolderNoCheck(name)

    @classmethod
    def getRootFolder(cls):
        return cls.objects.get(pk=1)

    @classmethod
    def getFolderNoCheck(cls, path, workingFolder='/', requestingUser=None):
        tree = getFolderTree()
        absPath = os.path.normpath(os.path.join(workingFolder, path))
        absPath = absPath[1:] # strip leading '/'
        if absPath != '':
            elts = absPath.split('/')
        else:
            elts = []
        current = tree.root
        for elt in elts:
            if requestingUser and not current.isAllowed(requestingUser, Action.LIST):
                raise PermissionDenied("while trying to access folder '%s' from working folder '%s': user %s is not allowed to list folder '%s'"
                                       % (path, workingFolder, requestingUser.username, current.path))
            try:
                current = current.subFolders[elt]
            except KeyError:
                raise ObjectDoesNotExist("while trying to access folder '%s' from working folder '%s': folder '%s' does not exist"
                                         % (path, workingFolder, os.path.normpath(os.path.join(current.path, elt))))
        return current

    @classmethod
    def getFolder(cls, requestingUser, path, workingFolder='/'):
        return cls.getFolderNoCheck(path, workingFolder, requestingUser=requestingUser)

    @classmethod
    def mkdirNoCheck(cls, path, workingFolder='/'):
        dirname, basename = os.path.split(path)
        parent = cls.getFolderNoCheck(dirname, workingFolder)
        return parent.makeSubFolderNoCheck(basename)

    @classmethod
    def mkdir(cls, requestingUser, path, workingFolder='/'):
        dirname, basename = os.path.split(path)
        parent = cls.getFolder(requestingUser, dirname, workingFolder)
        return parent.makeSubFolder(requestingUser, basename)

    @classmethod
    def rmdirNoCheck(cls, path, workingFolder='/'):
        dirname, basename = os.path.split(path)
        parent = cls.getFolderNoCheck(dirname, workingFolder)
        return parent.removeSubFolderNoCheck(basename)

    @classmethod
    def rmdir(cls, requestingUser, path, workingFolder='/'):
        dirname, basename = os.path.split(path)
        parent = cls.getFolder(requestingUser, workingFolder)
        return parent.removeSubFolder(requestingUser, basename)

class AgentPermission(models.Model):
    folder = models.ForeignKey(Folder, db_index=True)
    canView = models.BooleanField(db_index=True)
    canList = models.BooleanField(db_index=True)
    canAdd = models.BooleanField(db_index=True)
    canDelete = models.BooleanField(db_index=True)
    canChange = models.BooleanField(db_index=True)
    canManage = models.BooleanField(db_index=True)

    class Meta:
        abstract = True

    @staticmethod
    def getActionField(action):
        return 'can' + ACTION_LOOKUP[action].capitalize()

    def allows(self, action):
        return getattr(self, self.getActionField(action))

    @classmethod
    def allowing(cls, action):
        return cls.objects.filter(**{cls.getActionField(action): True})

    def setActions(self, actions):
        for action in Actions.ALL:
            setattr(self, self.getActionField(action), action in actions)

    def getActions(self):
        text = []
        for action in Actions.ALL:
            if self.allows(action):
                text.append(action)
        return ''.join(text)

class UserPermission(AgentPermission):
    user = models.ForeignKey(User, db_index=True)

    def __unicode__(self):
        return ('folder %s allows user %s the actions: %s' %
                (self.folder.name,
                 self.user.username,
                 self.getActions()))

class GroupPermission(AgentPermission):
    group = models.ForeignKey(Group, db_index=True)

    def __unicode__(self):
        return ('folder %s allows group %s the actions: %s' %
                (self.folder.name,
                 self.group.name,
                 self.getActions()))

class PermissionManager(object):
    @staticmethod
    def isAllowed(obj, user, action):
        if isinstance(obj, Folder):
            return obj.isAllowed(user, action)
        elif hasattr(obj, 'folder'):
            return obj.folder.isAllowed(user, action)
        else:
            raise TypeError('expected a Folder or a model with a folder field')

    @staticmethod
    def assertAllowed(obj, user, action):
        if isinstance(obj, Folder):
            obj.assertAllowed(user, action)
        elif hasattr(obj, 'folder'):
            obj.folder.assertAllowed(user, action)
        else:
            raise TypeError('expected a Folder or a model with a folder field')

    @staticmethod
    def filterAllowed(querySet, requestingUser, action=Action.VIEW):
        if (not settings.GEOCAM_USERS_ACCESS_CONTROL_ENABLED
            or ((requestingUser is not None) and requestingUser.is_superuser)):
            return querySet
        else:
            allowedFolderIds = getAllowedFolders(requestingUser, action).iterkeys()
            return querySet.filter(folder__id__in=allowedFolderIds)

    @classmethod
    def saveAssertAllowed(cls, obj, requestingUser, *args, **kwargs):
        if obj.pk is not None:
            cls.assertAllowed(obj, requestingUser, Action.CHANGE)
        else:
            cls.assertAllowed(obj, requestingUser, Action.ADD)
        obj.save(*args, **kwargs)

    @classmethod
    def deleteAssertAllowed(cls, obj, requestingUser, *args, **kwargs):
        cls.assertAllowed(obj, requestingUser, Action.DELETE)
        obj.delete(*args, **kwargs)

class FolderMember(object):
    """
    This mixin class is intended for objects that are 'contained' in a
    folder and subject to its access controls.

    It imports functions from PermissionManager for convenience.  You
    don't need to use this mixin if you prefer to call the
    PermissionManager functions directly.
    """

    def isAllowed(self, user, action):
        return self.folder.isAllowed(user, action)

    def assertAllowed(self, user, action):
        self.folder.assertAllowed(user, action)

    @classmethod
    def allowed(cls, requestingUser, action=Action.VIEW):
        return PermissionManager.filterAllowed(cls.objects, requestingUser, action)

    def saveAssertAllowed(self, requestingUser, *args, **kwargs):
        PermissionManager.saveAssertAllowed(self, requestingUser, *args, **kwargs)

    def deleteAssertAllowed(self, requestingUser, *args, **kwargs):
        PermissionManager.deleteAssertAllowed(self, requestingUser, *args, **kwargs)

class FolderMemberExample(models.Model, FolderMember):
    """
    This model exists only to support testing the FolderMember mixin.
    """
    name = models.CharField(max_length=32)
    folder = models.ForeignKey(Folder, db_index=True)
