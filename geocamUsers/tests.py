# __BEGIN_LICENSE__
# Copyright (C) 2008-2010 United States Government as represented by
# the Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
# __END_LICENSE__

import re
import time

from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied

from geocamUsers.models import getCacheKey, getWithCache, Folder, Action, Actions
from geocamUsers.models import FolderMemberExample as Member

class CacheTest(TestCase):
    def test_getCacheKey(self):
        def func(*args):
            return

        self.assertEquals("geocamUsers.tests.func.1.%7B%7D.%27hello%27",
                          getCacheKey(func, (1, {}, 'hello')))

    def test_getWithCache(self):
        def getX():
            return self.x

        self.x = 0
        self.assertEquals(0, getWithCache(getX, (), timeout=0.01))
        self.x = 1
        self.assertEquals(0, getWithCache(getX, (), timeout=0.01))
        time.sleep(0.01)
        self.assertEquals(1, getWithCache(getX, (), timeout=0.01))

class FolderTest(TestCase):
    def makeFolderWithPerms(self, agent, actionsName):
        root = Folder.getRootFolder()
        actions = getattr(Actions, actionsName.upper())
        prefix = re.sub('^\w+:', '', agent)
        folder = root.makeSubFolderNoCheck('%s_%s' % (prefix, actionsName))
        folder.clearAclNoCheck()
        folder.setPermissionsNoCheck(agent, actions)
        Member(name='foo', folder=folder).save() # to test reading
        return folder

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', password='12345')
        self.alice = User.objects.create_user('alice', 'alice@example.com')
        self.bob = User.objects.create_user('bob', 'bob@example.com')
        self.clara = User.objects.create_user('clara', 'clara@example.com')
        self.dave = User.objects.create_user('dave', 'dave@example.com')

        root = Folder.getRootFolder()
        self.f1 = root.makeSubFolderNoCheck('f1')
        self.f1.setPermissionsNoCheck(self.alice, Actions.ALL)
        self.f1.setPermissionsNoCheck(self.bob, Actions.WRITE)
        self.f1.setPermissionsNoCheck(self.clara, Actions.READ)
        self.f1.setPermissionsNoCheck('group:anyuser', Actions.NONE)

        levels = ('all', 'write', 'read', 'none')

        self.anyuserDir = {}
        for level in levels:
            self.anyuserDir[level] = self.makeFolderWithPerms('group:anyuser', level)

        self.authuserDir = {}
        for level in levels:
            self.authuserDir[level] = self.makeFolderWithPerms('group:authuser', level)

    def test_addObject(self):
        # admin, alice and bob have write privileges
        Member(name='byAdmin', folder=self.f1).saveAssertAllowed(self.admin)
        self.assert_(Member.objects.filter(name='byAdmin', folder=self.f1).exists())
        
        Member(name='byAlice', folder=self.f1).saveAssertAllowed(self.alice)
        self.assert_(Member.objects.filter(name='byAlice', folder=self.f1).exists())

        Member(name='byBob', folder=self.f1).saveAssertAllowed(self.bob)
        self.assert_(Member.objects.filter(name='byBob', folder=self.f1).exists())

        # clara only has read privileges, denied
        def byClara():
            Member(name='byClara', folder=self.f1).saveAssertAllowed(self.clara)
        self.assertRaises(PermissionDenied, byClara)

    def test_mkdir(self):
        # in these cases the getFolder() call should raise an exception if
        # the mkdir did not create the dir successfully

        # admin, alice, and bob have write privileges
        Folder.mkdir(self.admin, '/f1/byAdmin')
        Folder.getFolder(self.admin, '/f1/byAdmin')

        Folder.mkdir(self.alice, '/f1/byAlice')
        Folder.getFolder(self.alice, '/f1/byAlice')

        Folder.mkdir(self.bob, '/f1/byBob')
        Folder.getFolder(self.bob, '/f1/byBob')
        
        # clara has only read privileges, denied
        def byClara():
            Folder.mkdir(self.clara, '/f1/byClara')
        self.assertRaises(PermissionDenied, byClara)

    def test_viewObject(self):
        Member(name='x', folder=self.f1).save()
        def containsX(querySet):
            return querySet.filter(name='x', folder=self.f1).exists()
        
        # admin, alice, bob, and clara have read privileges
        self.assert_(containsX(Member.allowed(self.admin)))
        self.assert_(containsX(Member.allowed(self.alice)))
        self.assert_(containsX(Member.allowed(self.bob)))
        self.assert_(containsX(Member.allowed(self.clara)))

        # dave has no privileges, denied
        self.assertFalse(containsX(Member.allowed(self.dave)))

    def doTestFor(self, dirDict, requestingUser):
        # changing acl should work on 'all' but not on 'write'
        dirDict['all'].setPermissions(requestingUser, self.alice, Actions.READ)
        self.assert_(dirDict['all'].isAllowed(self.alice, Action.VIEW))

        def changeAclWrite():
            dirDict['write'].setPermissions(requestingUser, self.alice, Actions.READ)
        self.assertRaises(PermissionDenied, changeAclWrite)

        # adding an object should work on 'write' but not on 'read'
        Member(name='writeGood', folder=dirDict['write']).saveAssertAllowed(requestingUser)
        self.assert_(Member.objects.filter(name='writeGood', folder=dirDict['write']).exists())
        
        def addObjectRead():
            Member(name='writeBad', folder=dirDict['read']).saveAssertAllowed(requestingUser)
        self.assertRaises(PermissionDenied, addObjectRead)

        # viewing an object should work on 'read' but not on 'none'
        self.assert_(Member.allowed(requestingUser).filter(folder=dirDict['read']).exists())
        self.assertFalse(Member.allowed(requestingUser).filter(folder=dirDict['none']).exists())

    def test_anyuser(self):
        self.doTestFor(self.anyuserDir, None)

    def test_authuser(self):
        self.doTestFor(self.authuserDir, self.dave)
