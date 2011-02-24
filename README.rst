
The ``geocamFolder`` Django app defines hierarchical folders and
folder-based row-level access control inspired by AFS.

Folder Basics
~~~~~~~~~~~~~

The focus of the ``geocamFolder`` app is the ``Folder`` model.  Each
``Folder`` has a ``name`` and a ``parent`` pointer.  The ``parent``
relationship defines a familiar folder hierarchy::

  $ ./manage.py shell
  >>> from geocamFolder.models import Folder
  >>> Folder.mkdirNoCheck('/foo') # saves a new Folder in the db
  <Folder: foo parent=root>
  >>> Folder.mkdirNoCheck('/foo/bar')
  <Folder: bar parent=foo>
  >>> Folder.getFolderNoCheck('/foo/bar') # fetches a Folder from the db
  <Folder: bar parent=foo>

Folders are intended to serve as containers for other database objects.
They can provide a familiar hierarchical way for your users to organize
their data.  To make one of your models aware of folders, give it a
``ForeignKey(Folder)`` field and (optionally) make it inherit from the
``FolderMember`` mixin::

  from django.db import models
  from geocamFolder.models import Folder, FolderMember
  
  class FolderAwarePosition(models.Model, FolderMember):
      x = models.FloatField()
      y = models.FloatField()
      folder = models.ForeignKey(Folder)

      def __unicode__(self):
          return 'x=%s y=%s' % (self.x, self.y)

Now you can place instances of your model in a ``Folder``::

  $ ./manage.py shell
  >>> from geocamFolder.models import Folder
  >>> from myApp.models import FolderAwarePosition
  >>> p1 = FolderAwarePosition(x=0, y=1, folder=Folder.getRootFolder())
  >>> p1.save()
  >>> p2 = FolderAwarePosition(x=3, y=7, folder=Folder.getRootFolder())
  >>> p2.save()
  >>> FolderAwarePosition.objects.filter(folder=Folder.getRootFolder()).all()
  [<FolderAwarePosition: x=0.0 y=1.0>, <FolderAwarePosition: x=3.0 y=7.0>]

Access Control Basics
~~~~~~~~~~~~~~~~~~~~~

Besides organizing objects, the main purpose of folders is to provide a
flexible form of *row-level access control*, which is the ability to grant
users different permissions to different database objects, even different
instances of the same model.

As of version 1.2, the built-in Django authorization system only
provides model-level access control.  This means you can grant user
``alice`` permission to ``write``, ``insert``, or ``delete`` *all*
instances of your model ``Foo``, but there's no way to grant ``alice``
permission to ``write`` *some* instances of ``Foo`` but not others.

The ``geocamFolder`` approach to row-level access control is to provide
each ``Folder`` with an access control list (ACL) that grants access
privileges both to the ``Folder`` itself and to any other objects
contained in the Folder.  Here's an example::

  $ ./manage.py shell
  >>> from geocamFolder.models import Folder, Action
  >>> root = Folder.getRootFolder()
  >>> root.getAcl()
  {u'group:anyuser': 'vl'}
  >>> from django.contrib.auth.models import User
  >>> alice = User.objects.create_user('alice', 'alice@example.com')
  >>> root.isAllowed(alice, Action.VIEW)
  True
  >>> root.isAllowed(alice, Action.CHANGE)
  False
  >>> admin = User.objects.filter(is_superuser=True)[0]
  >>> root.isAllowed(admin, Action.CHANGE)
  True

Let's pull the example apart.  First, we asked what the ACL was for the
root folder.  The answer was ``{u'group:anyuser': 'vl'}``, which means
that any user can ``VIEW`` (``v``) objects in the folder and ``LIST``
(``l``) subfolders of the folder.  Those are the default permissions
for the root folder.

Then we created a new user ``alice`` and checked her permissions.  Like
all users, ``alice`` can use any permissions granted to
``group:anyuser``, so she can view the root folder, but she can't change
it.  However, super-users can always do anything, regardless of the ACL,
so ``admin`` *can* write to the root folder.

Access Control Lists
~~~~~~~~~~~~~~~~~~~~

An access control list is a dictionary mapping *agents* to *permission
sets*.  An agent is a user or a group (using the ``User`` and ``Group``
models of the built-in Django auth app), and a permission set is a
non-empty set of permissions from the following:

  ========== =============================================
  Permission Actions Controlled by Permission
  ========== =============================================
  VIEW       View objects in the folder
  LIST       List subfolders of the folder
  ADD        Add objects or subfolders to the folder
  DELETE     Delete objects or subfolders from the folder
  CHANGE     Change objects in the folder
  MANAGE     Change the folder ACL
  ========== =============================================

We have a standard string notation for permission sets: To show that a
permission is included in the set we put its first letter in the string,
so the permission set ``vl`` includes ``VIEW`` and ``LIST`` permissions.
There are some standard permission sets that are used so often we give
them nicknames in the ``Actions`` class:

  ========== ==============
  Nickname   Permission Set
  ========== ==============
  READ       vl
  WRITE      vladc
  ALL        vladcm
  ========== ==============
  
Here's a typical ACL for the ``/groups/basinFire/public`` folder.
Groups start with ``group:`` to distinguish them from users.  

  ===================== ===============
  Agent                 Permissions
  ===================== ===============
  group:authuser        vl (READ)
  group:basinFire       vladc (WRITE)
  tjones                vladc (WRITE)
  group:basinFireAdmins vladcm (ALL)
  ===================== ===============

A user *U* has a permission *P* if *P* has been granted in any of the
following ways:

 * By user: There is an entry in the ACL granting *P* to *U*.

 * By group: *U* is a member of a group *G*, and there is an entry in
   the ACL granting *P* to *G*. (Note that users can belong to multiple
   groups.)

 * By special group: All registered users are considered to belong to
   ``group:authuser``; by convention this membership is not recorded
   in the database.  Similarly, *all* users, even guests who have not
   logged in, belong to ``group:anyuser``.

Here's an advanced example of granting and revoking ACL permissions::

  $ ./manage.py shell
  >>> from django.contrib.auth.models import User, Group
  >>> alice = User.objects.create_user('alice', 'alice@example.com')
  >>> basinFireUsers = Group.objects.create(name='basinFireUsers')
  >>> alice.groups.add(basinFireUsers)
  
  >>> from geocamFolder.models import Folder, Action, Actions
  >>> f = Folder.mkdirNoCheck('/basinFire')
  >>> f.getAcl() # initial ACL inherited from parent folder
  {u'group:anyuser': 'vl'}
  >>> f.setPermissionsNoCheck(alice, Actions.WRITE)
  >>> f.getAcl()
  {u'alice': 'vladc', u'group:anyuser': 'vl'}
  >>> a = Folder.mkdir(alice, '/basinFire/alice')
  >>> a.getAcl() # initial ACL inherited + ALL access granted to requesting user
  {u'alice': 'vladcm', u'group:anyuser': 'vl'}
  
  >>> f.setPermissionsNoCheck(alice, Actions.NONE) # revoke alice's write access
  >>> f.getAcl()
  {u'group:anyuser': 'vl'}
  >>> f.rmdir(alice, '/basinFire/alice') # this won't work
  PermissionDenied: user alice does not have delete permission for folder basinFire
  >>> f.isAllowed(alice, Action.VIEW) # but alice can still view via group:anyuser
  True
  
  >>> f.setPermissionsNoCheck(basinFireUsers, Actions.WRITE)
  >>> f.getAcl()
  {u'group:anyuser': 'vl', u'group:basinFireUsers': 'vladc'}
  >>> f.isAllowed(alice, Action.DELETE) # now alice has delete permission via group:basinFireUsers
  True

Note that many functions in the ``Folder`` class have a "standard" and
"no-check" version.  The standard version takes the requesting user as
its first argument and checks that the user has permission to perform
the action (raising ``PermissionDenied`` if not).  The no-check version
leaves out the requesting user argument and does not check permissions.

To enforce proper access control, code that runs within a Django view
and performs actions on behalf of a user should typically use the
standard version of the function with ``request.user`` as the first
argument.  Administrative scripts might use the no-check version.  But
this is only a convention and usage is entirely up to you.

Objects Contained in Folders
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

[coming soon]

Limitations
~~~~~~~~~~~

Some ACL systems have the ability to explicitly deny access by
specifying negative rights to users who would normally have access
through one of their group memberships.  That feature is not supported
by ``geocamFolder``.

| __BEGIN_LICENSE__
| Copyright (C) 2008-2010 United States Government as represented by
| the Administrator of the National Aeronautics and Space Administration.
| All Rights Reserved.
| __END_LICENSE__
