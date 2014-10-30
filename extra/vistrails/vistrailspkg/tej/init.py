from __future__ import absolute_import, division

import tej

from vistrails.core.modules.config import ModuleSettings
from vistrails.core.modules.vistrails_module import Module, ModuleError, \
    ModuleSuspended
from vistrails.core.vistrail.job import JobMixin


assert __path__.endswith('.init')
this_pkg = __path__[:-5]


class QueueCache(object):
    """A global cache of RemoteQueue objects.
    """
    def __init__(self):
        self._cache = {}

    def get(self, destination, queue):
        key = destination, queue
        if key in self._cache:
            return self._cache[key]
        else:
            queue = tej.RemoteQueue(destination, queue)
            self._cache[key] = queue
            return queue

QueueCache = QueueCache()


class Queue(Module):
    """A connection to a queue on a remote server.

    `hostname` can be a hostname or a full destination in the format:
    ``[ssh://][user@]server[:port]``, e.g. ``vistrails@nyu.edu``.
    """
    _input_ports = [('hostname', '(basic:String)'),
                    ('username', '(basic:String)',
                     {'optional': True}),
                    ('port', '(basic:Integer)',
                     {'optional': True, 'defaults': "['22']"}),
                    ('queue', '(basic:String)',
                     {'optional': True, 'defaults': "['~/.tej']"})]
    _output_ports = [('queue', '(org.vistrails.extra.tej:Queue)')]

    def compute(self):
        destination = self.get_input('hostname')
        if self.has_input('username') or self.has_input('port'):
            destination = {'hostname': destination,
                           'username': self.get_input('username'),
                           'port': self.get_input('port')}
        queue = self.get_input('queue')
        self.set_output('queue', QueueCache.get(destination, queue))


class RemoteJob(object):
    def __init__(self, queue, job_id):
        self.queue = queue
        self.job_id = job_id

    @staticmethod
    def monitor_id(params):
        # Identifier for the JobMonitor
        return '%s/%s/%s' % (params['destination'],
                             params['queue'],
                             params['job_id'])

    def get_monitor_id(self):
        return self.monitor_id({'destination': self.queue.destination_string,
                                'queue': self.queue.queue,
                                'job_id': self.job_id})


class Job(Module):
    """A reference to a job in a queue.

    Objects represented by this type only represent completed jobs, since else,
    the creating module would have failed/suspended.

    You probably won't use this module directly since it references a
    pre-existing job by name.
    """
    _settings = ModuleSettings(abstract=True)
    _input_ports = [('id', '(basic:String)'),
                    ('queue', Queue)]
    _output_ports = [('job', '(org.vistrails.extra.tej:Job)'),
                     ('exitcode', '(basic:Integer)')]

    def compute(self):
        queue = self.get_input('queue')
        job_id = self.get_input('id')

        # Check job status
        try:
            status, arg = queue.status(job_id)
        except tej.JobNotFound:
            raise ModuleError(self, "Job not found")

        # Create job object
        job = RemoteJob(queue=queue, job_id=job_id)

        if status == tej.RemoteQueue.JOB_DONE:
            self.set_output('job', job)
            self.set_output('exitcode', int(arg))
        elif status == tej.RemoteQueue.JOB_RUNNING:
            raise ModuleSuspended(self, "Remote job is running",
                                  monitor=job)
        else:
            raise ModuleError(self, "Invalid job status %r" % status)


class BaseSubmitJob(JobMixin, Job):
    """Starts a job on a server.

    Thanks to the suspension/job tracking mechanism, this module does much more
    than start a job. If the job is running, it will suspend again. If the job
    is finished, you can obtain files from it.
    """
    _input_ports = [('id', '(basic:String)',
                     {'optional': True})]

    def make_id(self):
        """Makes a default identifier, using the pipeline signature.

        Unused if we have an explicit identifier.
        """
        if not hasattr(self, 'signature'):
            raise ModuleError(self,
                              "No explicit job ID and module has no signature")
        return "vistrails_module_%s" % self.signature

    def job_id(self, params):
        """Returns the job identifier that was provided, or calls make_id().
        """
        if 'job_id' not in params:
            params['job_id'] = self.make_id()
        return RemoteJob.monitor_id(params)

    def job_read_inputs(self):
        """Reads the input ports.
        """
        return {'destination': self.get_input('queue').destination_string,
                'queue': self.get_input('queue').queue,
                'job_id': self.get_input('id')}

    def job_start(self, params):
        """Submits a job.

        Reimplement in subclasses to actually submit a job.
        """
        raise NotImplementedError

    def job_get_monitor(self, params):
        """Gets a RemoteJob object to monitor a runnning job.
        """
        queue = QueueCache.get(params['destination'], params['queue'])
        return RemoteJob(queue, self.job_id(params))

    def job_finish(self, params):
        """Finishes job.

        Gets the exit code from the server.
        """
        queue = QueueCache.get(params['destination'], params['queue'])
        status, arg = queue.status(params['job_id'])
        assert status == tej.RemoteQueue.JOB_DONE
        params['exitcode'] = int(arg)

    def job_set_results(self, params):
        """Sets the output ports once the job is finished.
        """
        queue = QueueCache.get(params['destination'], params['queue'])
        self.set_output('exitcode', params['exitcode'])
        self.set_output('job', RemoteJob(queue, self.job_id(params)))


class SubmitJob(BaseSubmitJob):
    """Submits a generic job (a directory).
    """
    _input_ports = [('job', '(basic:Directory)'),
                    ('script', '(basic:String)',
                     {'optional': True, 'defaults': "['start.sh']"})]

    def job_start(self, params):
        """Sends the directory and submits the job.
        """
        queue = QueueCache.get(params['destination'], params['queue'])
        queue.submit(self.job_id(params),
                     self.get_input('directory'),
                     self.get_input('script'))
        return params


_modules = [Queue, Job, BaseSubmitJob, SubmitJob]
