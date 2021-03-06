import deepsecurity as api
from deepsecurity.rest import ApiException as api_exception
from threading import Thread
from threading import Lock
import copy
import codecs
import re
import time
import pickle
import os
import datetime

# DSM Host & port (must end in /api)
HOST = 'https://app.deepsecurity.trendmicro.com:443/api'
# API Key from the DSM defined in an environment variable called "API_KEY"
API_KEY = os.environ.get('API_KEY', None)
# Output file
FILENAME = 'report.csv'
# API Version
api_version = 'v1'


class DeepSecurityComputers:

    def __init__(self, config):
        self._lock = Lock()
        self._threadDataLock = Lock()
        self._threadsGroups = []
        self._threadCount = 12
        self._Groups = None
        self._Computers = []
        self._config = config



    def GetAllGroups(self, configuration):
        # Set search criteria
        search_criteria = api.SearchCriteria()
        search_criteria.id_value = 0
        search_criteria.id_test = "greater-than"
        # Create a search filter with maximum returned items
        page_size = 5000
        search_filter = api.SearchFilter()
        search_filter.max_items = page_size
        search_filter.search_criteria = [search_criteria]

        groupsapi = api.ComputerGroupsApi(api.ApiClient(configuration))

        paged_groups = []
        try:
            while True:
                t0 = time.time()
                groups = groupsapi.search_computer_groups(api_version, search_filter=search_filter)
                t1 = time.time()
                num_found = len(groups.computer_groups)
                if num_found == 0:
                    print("No groups found.")
                    break
                paged_groups.extend(groups.computer_groups)
                # Get the ID of the last group in the page and return it with the number of groups on the page
                last_id = groups.computer_groups[-1].id
                search_criteria.id_value = last_id
                print("Last ID: " + str(last_id), "Groups found: " + str(num_found))
                print ("Return rate: {0} groups/sec".format(num_found / (t1 - t0)))
                if num_found != page_size:
                    print ("Num_found {0} - Page size is {1}".format(num_found, page_size))

        except api_exception as e:
            return "Exception: " + str(e)

        return paged_groups

    def _GetGroupComputers(self, configuration, groupID):

        # Set search group criteria
        search_group_criteria = api.SearchCriteria()
        search_group_criteria.field_name = "groupID"
        if groupID:
            search_group_criteria.numeric_value = groupID
            search_group_criteria.numeric_test = "equal"
        else:
            search_group_criteria.null_test = True

        # Set search criteria
        search_criteria = api.SearchCriteria()
        search_criteria.id_value = 0
        search_criteria.id_test = "greater-than"

        # Create a search filter with maximum returned items
        page_size = 250
        search_filter = api.SearchFilter()
        search_filter.max_items = page_size
        search_filter.search_criteria = [search_criteria, search_group_criteria]

        # Perform the search and do work on the results
        computers_api = api.ComputersApi(api.ApiClient(configuration))
        paged_computers = []
        while True:
            try:
                t0 = time.time()
                computers = computers_api.search_computers(api_version, search_filter=search_filter)
                t1 = time.time()
                num_found = len(computers.computers)
                current_paged_computers = []

                if num_found == 0:
                    #This gets noise with so many threads
                    #print("No computers found.")
                    break

                for computer in computers.computers:
                    current_paged_computers.append(computer)

                paged_computers.append(current_paged_computers)

                # Get the ID of the last computer in the page and return it with the number of computers on the page
                last_id = computers.computers[-1].id
                search_criteria.id_value = last_id
                print("Last ID: " + str(last_id), "Computers found: " + str(num_found))
                print ("Return rate: {0} hosts/sec".format(num_found / (t1 - t0)))
                if num_found != page_size:
                    print ("Num_found {0} - Page size is {1}".format(num_found, page_size))

            except api_exception as e:
                print ("Exception: {0}".format(str(e)))

        return paged_computers

    def _computers_tread(self, configuration, groupID):
        computersReturn = self._GetGroupComputers(configuration=configuration, groupID=groupID)
        self._lock.acquire()
        self._Computers.extend(computersReturn)
        self._lock.release()

    def _computers_tread_array(self, configuration, groups):
        computerGroup = {}
        while True:
            self._threadDataLock.acquire()
            if self._threadsGroups:
                computerGroup = self._threadsGroups.pop()
                self._threadDataLock.release()
            else:
                self._threadDataLock.release()
                return

            if computerGroup:
                    self._computers_tread(configuration=configuration, groupID=computerGroup.id)
            else:
                return

        return

    def GetAllComputers(self):
        self._Groups = self.GetAllGroups(self._config)
        return self._GetAllComputers(self._config, self._Groups)

    def _GetAllComputers(self, configuration, groups):
        threads = []
        thread_data = {}
        self._threadsGroups = copy.copy(groups)


        t0 = time.time()
        # this starts a thread to collect all computers that do not belong to any group
        nonGroupcomputersThread = Thread(target=self._computers_tread, args=(configuration,None,))
        nonGroupcomputersThread.start()

        # Setup each thread
        for i in range(self._threadCount):
            threads.append(Thread(target=self._computers_tread_array, args=(configuration, None)))
        # Start each thread
        for i in range(self._threadCount):
            threads[i].start()
        #Wait for each thread
        for i in range(self._threadCount):
            threads[i].join()
        # if needed, wait for the nno-group thread to finish.
        nonGroupcomputersThread.join()
        t1 = time.time()
        # Give some total time/rate metrics.
        print ("Total time {0} seconds for a rate of {1}hosts/second".format(t1-t0, len(self._Computers)/(t1-t0)))
        return self._Groups,self._Computers


def WriteToDisk(computers, groups):
    with open('computers.pkl', 'wb') as outfile:
        pickle.dump(computers, outfile)
    with open('rest_groups.pkl', 'wb') as outfile:
        pickle.dump(groups, outfile)
    return


def ReadFromDisk():
    with open('rest_groups.pkl', 'rb') as infile:
        _Groups = pickle.load(infile)
    with open('computers.pkl', 'rb') as infile:
        _RestComputers = pickle.load(infile)
    return _Groups, _RestComputers


def ConvertToHostLight(value):
    if value == "active":
        return "Managed"
    if value == "warning":
        return "Warning"
    if value == "error":
        return "Critical"
    if value == "inactive":
        return "Unmanaged"
    if value == "not-supported":
        return "Unmanaged"
    return "Unmanaged"


def _getAmazonAccount(groupid, groups, _awsAccounts, _accountPattern):
    if groupid in _awsAccounts:
        return _awsAccounts[groupid]

    for g in groups:
        if g.id == groupid:
            if g.parent_group_id != None:
                cloudAccount = _getAmazonAccount(g.parent_group_id, groups, _awsAccounts, _accountPattern)
                _awsAccounts[g.id] = cloudAccount
                return cloudAccount
            if g.id in _awsAccounts:
                return _awsAccounts[g.name]
            _awsAccounts[g.id] = g.name
            return g.name

    return '0'


def _convertTimeStamp(serverTime):
    if serverTime:
        t =  datetime.datetime.fromtimestamp(serverTime / 1000).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        return t
    return " "

def WriteCSV(pagedcomputers, groups):
    _awsAccounts = {}
    _accountPattern = re.compile("[0-9]{6,25}")

    with codecs.open(FILENAME, "w", "utf-8") as outfile:
        outfile.write(
            "AWS Instance Id,Computer Status,Status,amazon_account_id,displayName,host_name, Agent Version, Last Agent Communication\n")
        for computers in pagedcomputers:
            for restComputer in computers:
                try:
                    account = _getAmazonAccount(restComputer.group_id,groups, _awsAccounts, _accountPattern)
                    statusMessage = "{0}".format(restComputer.computer_status.agent_status_messages)
                    statusMessage = statusMessage.replace(","," ")
                    if restComputer.ec2_virtual_machine_summary:
                        instanceid = restComputer.ec2_virtual_machine_summary.instance_id
                        if instanceid is None:
                             instanceid = "None"
                    else:
                        instanceid = "None"

                    outfile.write("{0},{1},{2},{3},{4},{5}, {6}, {7}\n".format(
                            instanceid,
                            ConvertToHostLight(restComputer.computer_status.agent_status),
                            statusMessage,
                            account,
                            restComputer.display_name,
                            restComputer.host_name,
                            restComputer.agent_version,
                            _convertTimeStamp(restComputer.last_agent_communication)
                        ))
                except Exception as err:
                    print (err)
    return



if __name__ == '__main__':
    if not API_KEY:
        raise ValueError('You must have "API_KEY" variable')
    # Add Deep Security Manager host information to the api client configuration
    configuration = api.Configuration()
    configuration.host = HOST
    configuration.verify_ssl = True
    # Authentication
    configuration.api_key['api-secret-key'] = API_KEY

    dsComputers = DeepSecurityComputers(configuration)
    groups,allComputers = dsComputers.GetAllComputers()
    WriteToDisk(allComputers, groups)
    # groups,allComputers = ReadFromDisk()
    WriteCSV(allComputers, groups)

print "finished"
