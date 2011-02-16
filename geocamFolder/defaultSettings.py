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

  from geocamFolderWeb import settings
  print settings.FOO

Don't try to get the value of FOO from django.conf.settings.  That
settings object will not know about the default value!
"""

GEOCAM_USERS_ACCESS_CONTROL_ENABLED = True

# the folder cache speeds things up, but you may want to disable it if
# you have multiple independent cache instances (for example, with the
# 'local memory' cache backend and multiple mod_wsgi daemons).
# otherwise, changes will take a while to propagate and users might be
# confused.
GEOCAM_USERS_FOLDER_CACHE_ENABLED = True
GEOCAM_USERS_FOLDER_CACHE_TIMEOUT_SECONDS = 30
