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

from geocamFolder.models import getCacheKey, getWithCache, Folder, Action, Actions
from geocamFolder.models import FolderMemberExample as Member

class CacheTest(TestCase):
    def test_getCacheKey(self):
        def func(*args):
            return

        self.assertEquals("1.geocamFolder.tests.func.1.%7B%7D.%27hello%27",
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
        folder = root.makeSubFolder('%s_%s' % (prefix, actionsName))
        folder.clearAcl()
        folder.setPermissions(agent, actions)

        # insert an object to the folder so we can test read access
        m = Member(name='foo')
        m.save()
        m.folders = [folder]
        m.save()

        return folder

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', password='12345')
        self.alice = User.objects.create_user('alice', 'alice@example.com')
        self.bob = User.objects.create_user('bob', 'bob@example.com')
        self.clara = User.objects.create_user('clara', 'clara@example.com')
        self.dave = User.objects.create_user('dave', 'dave@example.com')

        root = Folder.getRootFolder()
        self.f1 = root.makeSubFolder('f1')
        self.f1.setPermissions(self.alice, Actions.ALL)
        self.f1.setPermissions(self.bob, Actions.WRITE)
        self.f1.setPermissions(self.clara, Actions.READ)
        self.f1.setPermissions('group:anyuser', Actions.NONE)

        levels = ('all', 'write', 'read', 'none')

        self.anyuserDir = {}
        for level in levels:
            self.anyuserDir[level] = self.makeFolderWithPerms('group:anyuser', level)

        self.authuserDir = {}
        for level in levels:
            self.authuserDir[level] = self.makeFolderWithPerms('group:authuser', level)

    def test_insertObject(self):
        # admin, alice and bob have write privileges
        m = Member(name='byAdmin')
        m.saveAssertAllowed(self.admin, checkFolders=[self.f1])
        m.folders = [self.f1]
        m.save()
        self.assert_(Member.objects.filter(name='byAdmin', folders=self.f1).exists())
        
        m = Member(name='byAlice')
        m.saveAssertAllowed(self.alice, checkFolders=[self.f1])
        m.folders = [self.f1]
        m.save()
        self.assert_(Member.objects.filter(name='byAlice', folders=self.f1).exists())

        m = Member(name='byBob')
        m.saveAssertAllowed(self.bob, checkFolders=[self.f1])
        m.folders = [self.f1]
        m.save()
        self.assert_(Member.objects.filter(name='byBob', folders=self.f1).exists())

        # clara only has read privileges, denied
        def byClara():
            m = Member(name='byClara')
            m.saveAssertAllowed(self.clara, checkFolders=[self.f1])
            m.folders = [self.f1]
            m.save()
        self.assertRaises(PermissionDenied, byClara)

    def test_mkdir(self):
        # in these cases the getFolder() call should raise an exception if
        # the mkdir did not create the dir successfully

        # admin, alice, and bob have write privileges
        Folder.mkdirAssertAllowed(self.admin, '/f1/byAdmin')
        Folder.getFolderAssertAllowed(self.admin, '/f1/byAdmin')

        Folder.mkdirAssertAllowed(self.alice, '/f1/byAlice')
        Folder.getFolderAssertAllowed(self.alice, '/f1/byAlice')

        Folder.mkdirAssertAllowed(self.bob, '/f1/byBob')
        Folder.getFolderAssertAllowed(self.bob, '/f1/byBob')
        
        # clara has only read privileges, denied
        def byClara():
            Folder.mkdirAssertAllowed(self.clara, '/f1/byClara')
        self.assertRaises(PermissionDenied, byClara)

    def test_readObject(self):
        m = Member(name='x')
        m.save()
        m.folders = [self.f1]
        m.save()
        def containsX(querySet):
            return querySet.filter(name='x', folders=self.f1).exists()
        
        # admin, alice, bob, and clara have read privileges
        self.assert_(containsX(Member.allowed(self.admin)))
        self.assert_(containsX(Member.allowed(self.alice)))
        self.assert_(containsX(Member.allowed(self.bob)))
        self.assert_(containsX(Member.allowed(self.clara)))

        # dave has no privileges, denied
        self.assertFalse(containsX(Member.allowed(self.dave)))

    def doTestFor(self, dirDict, requestingUser):
        # changing acl should work on 'all' but not on 'write'
        dirDict['all'].setPermissionsAssertAllowed(requestingUser, self.alice, Actions.READ)
        self.assert_(dirDict['all'].isAllowed(self.alice, Action.READ))

        def changeAclWrite():
            dirDict['write'].setPermissionsAssertAllowed(requestingUser, self.alice, Actions.READ)
        self.assertRaises(PermissionDenied, changeAclWrite)

        # inserting an object should work on 'write' but not on 'read'
        m = Member(name='writeGood')
        m.saveAssertAllowed(requestingUser, checkFolders=[dirDict['write']])
        m.folders = [dirDict['write']]
        m.save()
        self.assert_(Member.objects.filter(name='writeGood', folders=dirDict['write']).exists())
        
        def insertObjectRead():
            m = Member(name='writeBad')
            m.saveAssertAllowed(requestingUser, checkFolders=[dirDict['read']])
            m.folders = [dirDict['read']]
            m.save()
        self.assertRaises(PermissionDenied, insertObjectRead)

        # reading an object should work on 'read' but not on 'none'
        self.assert_(Member.allowed(requestingUser).filter(folders=dirDict['read']).exists())
        self.assertFalse(Member.allowed(requestingUser).filter(folders=dirDict['none']).exists())

    def test_anyuser(self):
        self.doTestFor(self.anyuserDir, None)

    def test_authuser(self):
        self.doTestFor(self.authuserDir, self.dave)
