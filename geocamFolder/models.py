# __BEGIN_LICENSE__
# Copyright (C) 2008-2010 United States Government as represented by
# the Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
# __END_LICENSE__

import os
import operator
from cStringIO import StringIO

from django.db import models
from django.contrib.auth.models import User, Group
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.utils.http import urlquote

from geocamUtil.models.UuidField import UuidField
from geocamUtil.models.ExtrasField import ExtrasField
from geocamFolder import settings

ACTION_CHOICES = (
    'read',  # read members
    'list',  # list subfolders
    'insert',  # insert members
    'delete',  # delete members
    'change',  # change existing members
    'admin',  # change access control list
    )
ACTION_LOOKUP = dict([name[0], name] for name in ACTION_CHOICES)


class Action(object):
    READ = 'r'
    LIST = 'l'
    INSERT = 'i'
    DELETE = 'd'
    CHANGE = 'c'
    ADMIN = 'a'


# handy abbreviations for action combinations
class Actions(object):
    READ = 'rl'
    WRITE = 'rlidc'
    ALL = 'rlidca'
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
    if settings.GEOCAM_FOLDER_FOLDER_CACHE_ENABLED:
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
                        settings.GEOCAM_FOLDER_FOLDER_CACHE_TIMEOUT_SECONDS)


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
                        settings.GEOCAM_FOLDER_FOLDER_CACHE_TIMEOUT_SECONDS)


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
        app_label = 'geocamFolder'
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
        return (not settings.GEOCAM_FOLDER_ACCESS_CONTROL_ENABLED
                or (((user is not None) and user.is_superuser)
                    or (self.id in getAllowedFolders(user, action))))

    def getAcl(self):
        aclDict = {}
        for perm in UserPermission.objects.filter(folder=self):
            aclDict[perm.user.username] = perm.getActions()
        for perm in GroupPermission.objects.filter(folder=self):
            agentName = 'group:' + perm.group.name
            aclDict[agentName] = perm.getActions()
        return aclDict

    def getAclText(self):
        acl = self.getAcl().items()
        acl.sort()
        out = StringIO()
        for agentName, actions in acl:
            print >> out, '  %s %s' % (agentName, actions)
        return out.getvalue()

    def assertAllowed(self, user, action):
        if not self.isAllowed(user, action):
            if user is None:
                userName = '<anonymous>'
            else:
                userName = user.username
            raise PermissionDenied('user %s does not have %s permission for folder %s'
                                   % (userName, ACTION_LOOKUP[action], self.name))

    def setPermissions(self, agent, actions):
        if isinstance(agent, str):
            agent = getAgentByName(agent)

        if isinstance(agent, User):
            if actions == '':
                UserPermission.objects.filter(user=agent, folder=self).delete()
            else:
                perm, _created = UserPermission.objects.get_or_create(user=agent, folder=self)
                perm.setActions(actions)
                perm.save()
        elif isinstance(agent, Group):
            if actions == '':
                GroupPermission.objects.filter(group=agent, folder=self).delete()
            else:
                perm, _created = GroupPermission.objects.get_or_create(group=agent, folder=self)
                perm.setActions(actions)
                perm.save()
        else:
            raise TypeError('expected User, Group, or str')

    def setPermissionsAssertAllowed(self, requestingUser, agent, actions):
        self.assertAllowed(requestingUser, Action.ADMIN)
        self.setPermissions(agent, actions)

    def clearAcl(self):
        UserPermission.objects.filter(folder=self).delete()
        GroupPermission.objects.filter(folder=self).delete()

    def copyAcl(self, folder):
        self.clearAcl()
        for perm in UserPermission.objects.filter(folder=folder):
            newPerm = UserPermission(user=perm.user, folder=self)
            newPerm.setActions(perm.getActions())
            newPerm.save()
        for perm in GroupPermission.objects.filter(folder=folder):
            newPerm = GroupPermission(group=perm.group, folder=self)
            newPerm.setActions(perm.getActions())
            newPerm.save()

    def makeSubFolder(self, name, admin=None):
        # note: db-level uniqueness check will fail if the subdir already exists
        subFolder = Folder(name=name, parent=self)
        subFolder.save()

        subFolder.copyAcl(self)
        if admin:
            subFolder.setPermissions(admin, Actions.ALL)

        return subFolder

    def makeSubFolderAssertAllowed(self, requestingUser, name):
        self.assertAllowed(requestingUser, Action.INSERT)
        return self.makeSubFolder(name, admin=requestingUser)

    def removeSubFolder(self, name):
        Folder.objects.get(name=name, parent=self).delete()

    def removeSubFolderAssertAllowed(self, requestingUser, name):
        self.assertAllowed(requestingUser, Action.DELETE)
        return self.removeSubFolder(name)

    @classmethod
    def getRootFolder(cls):
        return cls.objects.get(pk=1)

    @classmethod
    def getFolder(cls, path, workingFolder='/', requestingUser=None):
        tree = getFolderTree()
        absPath = os.path.normpath(os.path.join(workingFolder, path))
        absPath = absPath[1:]  # strip leading '/'
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
    def getFolderAssertAllowed(cls, requestingUser, path, workingFolder='/'):
        return cls.getFolder(path, workingFolder, requestingUser=requestingUser)

    @classmethod
    def mkdir(cls, path, workingFolder='/'):
        dirname, basename = os.path.split(path)
        parent = cls.getFolder(dirname, workingFolder)
        return parent.makeSubFolder(basename)

    @classmethod
    def mkdirAssertAllowed(cls, requestingUser, path, workingFolder='/'):
        dirname, basename = os.path.split(path)
        parent = cls.getFolderAssertAllowed(requestingUser, dirname, workingFolder)
        return parent.makeSubFolderAssertAllowed(requestingUser, basename)

    @classmethod
    def rmdir(cls, path, workingFolder='/'):
        dirname, basename = os.path.split(path)
        parent = cls.getFolder(dirname, workingFolder)
        return parent.removeSubFolder(basename)

    @classmethod
    def rmdirAssertAllowed(cls, requestingUser, path, workingFolder='/'):
        dirname, basename = os.path.split(path)
        parent = cls.getFolderAssertAllowed(requestingUser, dirname, workingFolder)
        return parent.removeSubFolderAssertAllowed(requestingUser, basename)


class AgentPermission(models.Model):
    folder = models.ForeignKey(Folder, db_index=True)
    canRead = models.BooleanField(db_index=True)
    canList = models.BooleanField(db_index=True)
    canInsert = models.BooleanField(db_index=True)
    canDelete = models.BooleanField(db_index=True)
    canChange = models.BooleanField(db_index=True)
    canAdmin = models.BooleanField(db_index=True)

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
    @classmethod
    def isAllowedByAnyFolder(cls, folders, user, action):
        return reduce(operator.or_, [folder.isAllowed(user, action) for folder in folders])

    @classmethod
    def assertAllowedByAnyFolder(cls, folders, user, action):
        allowed = cls.isAllowedByAnyFolder(folders, user, action)
        if not allowed:
            if user is None:
                userName = '<anonymous>'
            else:
                userName = user.username
            raise PermissionDenied('user %s does not have %s permission for any folder in %s'
                                   % (userName, ACTION_LOOKUP[action], folders))

    @classmethod
    def isAllowed(cls, obj, user, action):
        if isinstance(obj, Folder):
            return obj.isAllowed(user, action)
        elif hasattr(obj, 'folders'):
            return cls.isAllowedByAnyFolder(obj.folders.all(), user, action)
        else:
            raise TypeError('expected a Folder or a model with a folders field')

    @classmethod
    def assertAllowed(cls, obj, user, action):
        if isinstance(obj, Folder):
            obj.assertAllowed(user, action)
        elif hasattr(obj, 'folders'):
            cls.assertAllowedByAnyFolder(obj.folders.all(), user, action)
        else:
            raise TypeError('expected a Folder or a model with a folders field')

    @classmethod
    def filterAllowed(cls, querySet, requestingUser, action=Action.READ):
        if (not settings.GEOCAM_FOLDER_ACCESS_CONTROL_ENABLED
            or ((requestingUser is not None) and requestingUser.is_superuser)):
            return querySet
        else:
            allowedFolderIds = getAllowedFolders(requestingUser, action).iterkeys()
            return querySet.filter(folders__in=allowedFolderIds)

    @classmethod
    def saveAssertAllowed(cls, obj, requestingUser, checkFolders=None, *args, **kwargs):
        if obj.pk is not None:
            cls.assertAllowed(obj, requestingUser, Action.CHANGE)
        else:
            assert checkFolders is not None, "saveAssertAllowed can't check if it's ok to save a new object unless checkFolders is specified"
            cls.assertFolderChangeAllowed(requestingUser, [], checkFolders)
        obj.save(*args, **kwargs)

    @classmethod
    def assertFolderChangeAllowed(cls, requestingUser, oldFolders, newFolders):
        # For objects already in the database, check that user has admin
        # permissions on the object (i.e. admin permissions on at least
        # one folder the object is already in). If not, the requesting
        # user could elevate another user's permissions by adding the
        # object to a new folder where they have more permissions. If
        # the object is not already in any folders that means this user
        # is creating it, so they have "initial" admin privileges until
        # the object's folders have been set.
        if oldFolders:
            cls.assertAllowedByAnyFolder(oldFolders, requestingUser, Action.ADMIN)

        # check that user has insert permissions for all folders the object is
        # being added to
        oldFolderDict = dict.fromkeys([f.id for f in oldFolders])
        for f in newFolders:
            if f.id not in oldFolderDict:
                f.assertAllowed(requestingUser, Action.INSERT)

        # check that user has delete permissions for all folders the object is
        # being removed from
        newFolderDict = dict.fromkeys([f.id for f in newFolders])
        for f in oldFolders:
            if f.id not in newFolderDict:
                f.assertAllowed(requestingUser, Action.DELETE)

    @classmethod
    def deleteAssertAllowed(cls, obj, requestingUser, *args, **kwargs):
        cls.assertFolderChangeAllowed(requestingUser, obj.folders.all(), [])
        obj.delete(*args, **kwargs)


class FolderMember(object):
    """
    This mixin class is intended for objects that are 'contained' in
    folders and subject to their access controls.

    It imports functions from PermissionManager for convenience.  You
    don't need to use this mixin if you prefer to call the
    PermissionManager functions directly.
    """

    def isAllowed(self, requestingUser, action):
        return PermissionManager.isAllowed(self, requestingUser, action)

    def assertAllowed(self, requestingUser, action):
        PermissionManager.assertAllowed(self, requestingUser, action)

    @classmethod
    def allowed(cls, requestingUser, action=Action.READ):
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
    folders = models.ManyToManyField(Folder, db_index=True)


class FolderAwarePosition(models.Model, FolderMember):
    """
    This model appears as an example in the documentation.  We
    instantiate it here to make it easier to verify the docs
    are accurate.
    """
    x = models.FloatField()
    y = models.FloatField()
    folders = models.ManyToManyField(Folder, db_index=True)

    def __unicode__(self):
        return 'x=%s y=%s' % (self.x, self.y)
