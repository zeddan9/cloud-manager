import os
import sys
import subprocess
from threading import Lock, Thread
import math
import time
import yaml
import docker
client = docker.from_env()
import etcd3
etcd = etcd3.client()
from ast import literal_eval

exitapp = False

# save running containers' information
docker_state = {}
# resource usage for auto scaling
lock = Lock()
cloud_usage= {}
auto_scaling_enable = False

# define threshold for auto scaling
CPU_UPPER_BOUND = 30
CPU_LOWER_BOUND = 5
MEMORY_UPPER_BOUND = 30
MEMORY_LOWER_BOUND = 5

def list_worker():
    print()
    if 'worker_name_list' in docker_state:
        for container in docker_state['worker_name_list']:
            print(container)
    else:
        print("Woker list does not exist yet.")
    update_docker_state()
    sync_etcd()
    print()

def start_worker(service):
    os.system("docker-compose up -d " + service)
    update_docker_state()
    sync_etcd()
    print()

def stop_worker(worker):
    os.system("docker container stop " + worker)
    update_docker_state()
    sync_etcd()
    print()

def scale_service(service, replica):
    os.system("docker-compose scale " + service + "=" + str(replica))
    update_docker_state()
    sync_etcd()
    print()

def check_supported_service():
    print("\n#######################################\n")
    print ("Supported services are listed as below:\n")
    with open("docker-compose.yml", 'r') as stream:
        try:
            supported_service_dict = yaml.load(stream)['services']
            for key in supported_service_dict:
                print (key)
        except yaml.YAMLError as exc:
            print(exc)
    print("\n#######################################\n")
    # write to etcd
    update_docker_state()
    sync_etcd()

# get docker stats and store it as a dictionary in cloud_stats
def get_docker_stats():
    # save information from "docker ps"
    global auto_scaling_enable
    global exitapp

    while not exitapp:

        if auto_scaling_enable:
            global cloud_usage
            lock.acquire()
            cloud_usage = {}

            p = subprocess.Popen(['docker', 'stats', '--no-stream'], stdout=subprocess.PIPE, bufsize=1)
            for line in iter(p.stdout.readline, b''):
                line_split = line.decode("utf-8").split(' ')
                line_split = [x for x in line_split if x is not '']
                cloud_usage[line_split[1]] = line_split[2:]
            p.stdout.close()

            # delete the entry obtained from title
            if 'ID' in cloud_usage:
                del cloud_usage['ID']

            running_service = {}

            # get number of contaiers of each service
            for k in cloud_usage:
                service_name = k.split('_')[1]
                if service_name not in running_service:
                    running_service[service_name] = 1
                else:
                    running_service[service_name] += 1

            # check if service needs to be scaled up or down
            for k, v in cloud_usage.items():
                service_name = k.split('_')[1]
                cpu_usage_percent = float(v[0][0:-1])
                memory_usage_percent = float(v[4][0:-1])
                if cpu_usage_percent > CPU_UPPER_BOUND or memory_usage_percent > MEMORY_UPPER_BOUND:
                    num_will_add = max(cpu_usage_percent/CPU_UPPER_BOUND, memory_usage_percent/MEMORY_UPPER_BOUND)
                    # subprocess.run(['docker-compose', 'scale', service_name + "=" + str(num_will_add + running_service[service_name])])
                    os.system("docker-compose scale " + service_name + "=" + str(math.ceil(num_will_add + running_service[service_name])))
                elif (cpu_usage_percent < CPU_LOWER_BOUND and memory_usage_percent < MEMORY_UPPER_BOUND/2) or (cpu_usage_percent < CPU_UPPER_BOUND/2 and memory_usage_percent < MEMORY_LOWER_BOUND):
                    if running_service[service_name] > 1:
                        # subprocess.run(['docker-compose', 'scale', service_name + "=" + str(running_service[service_name] - 1)])
                        os.system("docker-compose scale " + service_name + "=" + str(running_service[service_name] - 1))

            lock.release()
            time.sleep(5)

# stop all running containers
def stop_all():
    global auto_scaling_enable
    print("Stopping all running services...")
    os.system("docker-compose stop")
    auto_scaling_enable = False
    update_docker_state()
    sync_etcd()
    print()

def exit_manager():
    global exitapp 
    exitapp = True
    exit()

# update docker state maintained in manager's RAM
def update_docker_state():
    name_list = []
    for container in client.containers.list():
        name_list.append(container.name)
    docker_state['worker_name_list'] = name_list

# write to etcd
def sync_etcd():
    # write container list to etcd with key "worker_name_list"
    name_list = []
    for container in client.containers.list():
        name_list.append(container.name)
    etcd.put('worker_name_list', str(name_list))

    # write auto_scaling_enable to etcd with key "auto_scaling_enable"
    global auto_scaling_enable
    etcd.put('auto_scaling_enable', str(auto_scaling_enable))

# read from etcd to manager's RAM
def recover_from_etcd():
    # recover worker name list
    worker_name_list = etcd.get('worker_name_list')
    if worker_name_list != (None, None):
        print("Syncing with etcd...\n")
        docker_state['worker_name_list'] = literal_eval(str(worker_name_list[0])[2:-1])
    else:
        print("No data found in etcd.\n")

    # recover auto scaling enable flag
    global auto_scaling_enable
    flag = etcd.get('auto_scaling_enable')
    flag = str(flag[0])[2:-1]
    if flag == 'False':
        auto_scaling_enable = False
    elif flag == 'True':
        auto_scaling_enable = True
    else:
        auto_scaling_enable = False

    print("Sync done!\n")


def cli():

    global auto_scaling_enable
    global cloud_usage

    print("Docker Manager Menu:\n"
        + "1. List workers\n"
        + "2. Start a worker\n"
        + "3. Stop a worker\n"
        + "4. Scale a service\n"
        + "5. Enable auto-scaling\n"
        + "6. Disable auto-scaling\n"
        + "7. Check supported services\n"
        + "8. Stop all services\n"
        + "9. Exit\n"    )

    operation = input("Enter the operation number: ")

    # list workers
    if operation == '1':
        list_worker()
        # lock.acquire()
        # print(cloud_usage)
        # lock.release()

    # start a worker
    elif operation == '2':
        check_supported_service()
        service = input("Which service do you want the worker to run: ")
        start_worker(service)

    # stop a worker
    elif operation == '3':
        list_worker()
        worker = input("Which worker do you want to stop: ")
        stop_worker(worker)

    # scale a service
    elif operation == '4':
        check_supported_service()
        service = input("Which service do you want to scale up?\n"
            "Please specify the service and number of workers in format <service num>: ")
        arg = service.split(" ")
        scale_service(arg[0], arg[1])

    # Enable auto scaling
    elif operation == '5':
        if auto_scaling_enable is not True:
            auto_scaling_enable = True
        else:
            print("\nAuto scaling is already enabled.\n")
        update_docker_state()
        sync_etcd()

    # Disable auto scaling
    elif operation == '6':
        if auto_scaling_enable is not False:
            auto_scaling_enable = False
            lock.acquire()
            cloud_usage = {}
            lock.release()
        else:
            print("\nAuto scaling is already disabled.\n")
        update_docker_state()
        sync_etcd()
        
    # check supported services
    elif operation == '7':
        check_supported_service()
    
    # stop all running services
    elif operation == '8':
        stop_all()

    # exit
    elif operation == '9':
        exit_manager()

def main():

    recover_from_etcd()

    t = Thread(target=get_docker_stats)
    t.start()

    while True:
        cli()
 
if __name__ == "__main__":
    try:
        main()
    except(KeyboardInterrupt, SystemError):
        exitapp = True
        raise