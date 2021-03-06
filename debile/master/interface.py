# Copyright (c) 2012-2013 Paul Tagliamonte <paultag@debian.org>
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from debile.master.server import user_method, builder_method, NAMESPACE
from debile.master.orm import (Job, Arch, Check, Builder, Source, Binary,
                               JobDependencies)
from debile.master.core import config
from debile.master.messaging import emit
from debile.utils.keys import import_key

from sqlalchemy import exists, or_
import datetime as dt


class DebileMasterInterface(object):
    """
    This is the exposed interface for the builders. Code enhacing the server
    should likely go here, unless you know what you're doing.
    """

    # Simple stuff.

    @builder_method
    def builder_whoami(self):
        """
        ID check
        """
        return NAMESPACE.machine.name

    @user_method
    def user_whoami(self):
        """
        ID check
        """
        return NAMESPACE.user.name

    # The following trio of methods handle the job control.

    @builder_method
    def get_next_job(self, suites, arches, capabilities):
        arches_conditions = [ Arch.name == a for a in arches ]
        arches = [
            x.id for x in NAMESPACE.session.query(Arch).filter(
                or_(*arches_conditions)
            ).all()
        ]
        # This horseshit nonsense is due to SQLAlchemy not doing
        # the sane thing with Job.affinity.name.in_. Nonsense. Horseshit.

        job = NAMESPACE.session.query(Job).filter(
            Job.assigned_at==None,
            Job.finished_at==None,
            Job.arch_id.in_(arches),
            Job.affinity_id.in_(arches) | Job.affinity_id==None,
        ).outerjoin(Job.depedencies).filter(
            JobDependencies.id==None
        ).first()

        if job is None:
            return None

        job.assigned_at = dt.datetime.utcnow()
        job.builder = NAMESPACE.machine
        NAMESPACE.session.add(job)
        NAMESPACE.session.commit()

        emit('start', 'job', job.debilize())

        return job.debilize()

    @builder_method
    def close_job(self, job_id, failed):
        job = NAMESPACE.session.query(Job).get(job_id)
        job.finished_at = dt.datetime.utcnow()
        # We don't actually close the job here because we wait until
        # we accept the DUD (so that we have the artifacts to actually
        # give the job out to new nodes), so avoid job.close()

        NAMESPACE.session.add(job)
        NAMESPACE.session.commit()

        emit('complete', 'job', job.debilize())

        return True

    @builder_method
    def forfeit_job(self, job_id):
        job = NAMESPACE.session.query(Job).get(job_id)
        job.assigned_at = None
        job.builder = None
        NAMESPACE.session.add(job)
        NAMESPACE.session.commit()

        emit('abort', 'job', job.debilize())

        return True

    # Useful methods below.

    def get_source(self, source_id):
        return NAMESPACE.session.query(Source).get(source_id).debilize()

    def get_binary(self, binary_id):
        return NAMESPACE.session.query(Binary).get(binary_id).debilize()

    def get_archive_location(self, group_name):
        archive_path_tmpl = config['repo']['archive_location']
        path = archive_path_tmpl.format(url=config['repo']['url'],
                                             group=group_name,)
        return path

    def get_info(self):
        return {
            "repo": {
                "base": config['repo']['url']
            },
        }

    def job_count(self):
        """
        Work out the job count.
        """
        return NAMESPACE.session.query(Job).count()

    @user_method
    def create_builder(self, slave_name, slave_password, key):
        keyid = import_key(key)

        obid = NAMESPACE.session.query(Builder).filter_by(
            name=slave_name).count()

        if obid != 0:
            raise ValueError("Slave already exists.")

        b = Builder(maintainer=NAMESPACE.user, name=slave_name, key=keyid,
                    password=slave_password, last_ping=dt.datetime.utcnow())
        emit('create', 'slave', b.debilize())
        NAMESPACE.session.add(b)
        NAMESPACE.session.commit()
        return b.debilize()
