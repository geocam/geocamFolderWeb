# __BEGIN_LICENSE__
# Copyright (C) 2008-2010 United States Government as represented by
# the Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
# __END_LICENSE__

"""
This app may define some new parameters that can be modified in the
Django settings module.  Let's say one such parameter is FOO.  The
default value for FOO is defined in this file, like this:

  FOO = 'my default value'

If the admin for the site doesn't like the default value, they can
override it in the site-level settings module, like this:

  FOO = 'a better value'

Other modules can access the value of FOO like this:

  from geocamUsersWeb import settings
  print settings.FOO

Don't try to get the value of FOO from django.conf.settings.  That
settings object will not know about the default value!
"""

GEOCAM_USERS_ACTION_CHOICES = (
    (0, 'view'), # view members
    (1, 'list'), # list subfolders; if denied prevents all access to subfolders
    (2, 'insert'), # insert members
    (3, 'delete'), # delete members
    (4, 'change'), # change existing members
    (5, 'admin'), # change access control list

    # can add more action choices in site-level settings if needed, but
    # don't modify the preceding ones or the initial_data fixture will
    # break.
    )

GEOCAM_USERS_PERMISSION_CACHE_TIMEOUT_SECONDS = 30
