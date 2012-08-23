########################################################################
# $HeadURL$
# File :    JobCleaningAgent.py
# Author :  A.T.
########################################################################
"""
The Job Cleaning Agent controls removing jobs from the WMS in the end of their life cycle.
"""
__RCSID__ = "$Id$"

from DIRAC.Core.Base.AgentModule                      import AgentModule
from DIRAC.WorkloadManagementSystem.DB.JobDB          import JobDB
from DIRAC.WorkloadManagementSystem.DB.TaskQueueDB    import TaskQueueDB
from DIRAC                                            import S_OK, S_ERROR, gLogger
from DIRAC.WorkloadManagementSystem.Client.SandboxStoreClient  import SandboxStoreClient
import DIRAC.Core.Utilities.Time as Time
import string
import time

REMOVE_STATUS_DELAY = { 'Done':14,
                        'Killed':7,
                        'Failed':14 }

class JobCleaningAgent( AgentModule ):
  """
      The specific agents must provide the following methods:
      - initialize() for initial settings
      - beginExecution()
      - execute() - the main method called in the agent cycle
      - endExecution()
      - finalize() - the graceful exit of the method, this one is usually used
                 for the agent restart
  """

  #############################################################################
  def initialize( self ):
    """Sets defaults
    """

    self.am_setOption( "PollingTime", 60 )
    self.jobDB = JobDB()
    self.taskQueueDB = TaskQueueDB()
    # self.sandboxDB = SandboxDB( 'SandboxDB' )
    self.prod_types = self.am_getOption('ProductionTypes',['DataReconstruction', 'DataStripping', 'MCSimulation', 'Merge', 'production'])
    gLogger.info('Will exclude the following Production types from cleaning %s'%(string.join(self.prod_types,', ')))
    self.maxJobsAtOnce = self.am_getOption('MaxJobsAtOnce',200)
    self.jobByJob = self.am_getOption('JobByJob',True)
    self.throttlingPeriod = self.am_getOption('ThrottlingPeriod',0.)
    return S_OK()

  def __getAllowedJobTypes( self ):
    #Get valid jobTypes
    result = self.jobDB.getDistinctJobAttributes( 'JobType' )
    if not result[ 'OK' ]:
      return result
    cleanJobTypes = []
    for jobType in result[ 'Value' ]:
      if jobType not in self.prod_types:
        cleanJobTypes.append( jobType )
    self.log.notice( "JobTypes to clean %s" % cleanJobTypes )
    return S_OK( cleanJobTypes )

  #############################################################################
  def execute( self ):
    """The PilotAgent execution method.
    """
    #Delete jobs in "Deleted" state
    result = self.removeJobsByStatus( { 'Status' : 'Deleted' } )
    if not result[ 'OK' ]:
      return result
    #Get all the Job types that can be cleaned
    result = self.__getAllowedJobTypes()
    if not result[ 'OK' ]:
      return result
    baseCond = { 'JobType' : result[ 'Value' ] }
    # Remove jobs with final status
    for status in REMOVE_STATUS_DELAY:
      delay = REMOVE_STATUS_DELAY[ status ]
      condDict = dict( baseCond )
      condDict[ 'Status' ] = status
      delTime = str( Time.dateTime() - delay * Time.day )
      result = self.removeJobsByStatus( condDict, delTime )
      if not result['OK']:
        gLogger.warn( 'Failed to remove jobs in status %s' % status )
    return S_OK()

  def removeJobsByStatus( self, condDict, delay = False ):
    """ Remove deleted jobs
    """
    if delay:
      gLogger.verbose( "Removing jobs with %s and older than %s" % ( condDict, delay ) )
      result = self.jobDB.selectJobs( condDict, older = delay, limit = self.maxJobsAtOnce )
    else:
      gLogger.verbose( "Removing jobs with %s " % condDict )
      result = self.jobDB.selectJobs( condDict, limit = self.maxJobsAtOnce )

    if not result['OK']:
      return result

    jobList = result['Value']
    if len(jobList) > self.maxJobsAtOnce:
      jobList = jobList[:self.maxJobsAtOnce]
    if not jobList:
      return S_OK()

    self.log.notice( "Deleting %s jobs for %s" % ( len( jobList ), condDict ) )

    count = 0
    error_count = 0
    result = SandboxStoreClient( useCertificates = True ).unassignJobs( jobList )
    if not result[ 'OK' ]:
      gLogger.warn( "Cannot unassign jobs to sandboxes", result[ 'Message' ] )

    if self.jobByJob:
      for jobID in jobList:
        resultJobDB = self.jobDB.removeJobFromDB( jobID )
        resultTQ = self.taskQueueDB.deleteJob( jobID )
        if not resultJobDB['OK']:
          gLogger.warn( 'Failed to remove job %d from JobDB' % jobID, result['Message'] )
          error_count += 1
        elif not resultTQ['OK']:
          gLogger.warn( 'Failed to remove job %d from TaskQueueDB' % jobID, result['Message'] )
          error_count += 1
        else:
          count += 1
        if self.throttlingPeriod:
          time.sleep(self.throttlingPeriod)  
    else:    
      result = self.jobDB.removeJobFromDB( jobList )
      if not result['OK']:
        gLogger.error('Failed to delete %d jobs from JobDB' % len(jobList) )
      else:
        gLogger.info('Deleted %d jobs from JobDB' % len(jobList) )
  
      for jobID in jobList:
        resultTQ = self.taskQueueDB.deleteJob( jobID )
        if not resultTQ['OK']:
          gLogger.warn( 'Failed to remove job %d from TaskQueueDB' % jobID, resultTQ['Message'] )
          error_count += 1
        else:
          count += 1    

    if count > 0 or error_count > 0 :
      gLogger.info( 'Deleted %d jobs from JobDB, %d errors' % ( count, error_count ) )
    return S_OK()
