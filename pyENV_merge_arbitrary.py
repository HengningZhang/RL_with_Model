import sumolib
import traci
import simpla
import os
import subprocess
import sys
import shutil
import random
import numpy as np
import time
import theta_calculation as tc
import queue
import generate_routefile_merge as gr
from sumolib import checkBinary
w_1 = 25.8 / 3600  # value of time ($/hour)
w_2 = 0.868			# oil price ($/L)
d1 = 1000.0			# the distance of d_1 (m)
d2=30000
collision_time_delay = 2
gamma=0.9


alpha = 3.51 * 10 ** (-7)
v = 24.0
eta = 0.1
k = 32.2 / 100000.0

def distance(coord1,coord2):
    return (abs(coord1[0]-coord2[0])**2+abs(coord1[1]-coord2[1])**2)**0.5
class lane:
    def __init__(self,ID):
        self.ID=ID
        self.time_interval_list=[]
        self.lead_time=0
        self.flow=0
    
    def reset(self):
        self.time_interval_list.clear()
        self.lead_time=0
        self.flow=0

class junction:
    def __init__(self, ID, incomingLanes,outgoingLanes, nodePosition):
        self.ID = ID
        self.incLanes = []
        self.incLanesOBJ=[]
        self.outLanes = []
        self.inflows={}
        self.inflow_rates={}
        self.outflows={}
        self.outflow_rates={}
        for lane in incomingLanes:
            laneid=lane.getID()
            self.incLanes.append(laneid)
            self.incLanesOBJ.append(lane)
            self.inflows[laneid]=queue.Queue(50)
            self.inflow_rates[laneid]=0
        for lane in outgoingLanes:
            laneid=lane.getID()
            self.outLanes.append(laneid)
            self.outflows[laneid]=queue.Queue(50)
            self.outflow_rates[laneid]=0
        self.nodePos=nodePosition
        self.lead_vehicle = None
        self.onLaneVehicles=[]
        self.lead_time = 0
        self.temp_vehicle = []
        self.temp_time = 0
        
        self.time_interval_list=[]
    
    def getSK(self):
        # calculate the predicted headway sk
        temp_time=traci.simulation.getTime()
        time_interval = temp_time - self.lead_time
        return time_interval
        
    def getTotalCost(self):
        all_vehicle_list = []
        for lane in self.incLanes:
            all_vehicle_list.append(traci.edge.getLastStepVehicleIDs(lane))
        time_delta = traci.simulation.getDeltaT()
        total_time=0
        total_fuel=0
        for item in all_vehicle_list:
            for vehicle_item in item:
                # vehicle_fuel_rate = traci.vehicle.getFuelConsumption(vehicle_item)
                vehicle_speed=traci.vehicle.getSpeed(vehicle_item)
                vehicle_fuel_rate = 3.51 * (10 ** (-4)) * (vehicle_speed ** 3) + 0.407 * vehicle_speed
                vehicle_item_type = traci.vehicle.getTypeID(vehicle_item)
                if (vehicle_item_type == 'connected_pFollower' or vehicle_item_type == 'connected_pCatchup' or vehicle_item_type == 'connected_pCatchupFollower'):
                    total_fuel += 0.9 * vehicle_fuel_rate * time_delta
                else:
                    total_fuel += vehicle_fuel_rate * time_delta
                total_time += time_delta
        total_cost = total_fuel / 1000.0 * w_2 + total_time * w_1
        return total_cost, total_fuel/1000, total_time


    def detectArrival(self):
        for lane in self.incLanes:
            if "link" in lane:
                if traci.inductionloop.getLastStepVehicleIDs("e1Detector"+lane):
                    return True
        return False

    def restrictDrivingMode(self):
        for i in range(len(self.incLanes)):
            for item in traci.edge.getLastStepVehicleIDs(self.incLanes[i]):
                if distance(self.nodePos,traci.vehicle.getPosition(item))/distance(self.nodePos,self.incLanesOBJ[i].getFromNode().getCoord()) > 1100/(self.incLanesOBJ[i].getLength()):
                    
                    traci.vehicle.setSpeedMode(item,1)
                    traci.vehicle.setSpeed(item, 24.0)


    def coordinate(self,chase):
        self.temp_vehicle.clear()
        self.onLaneVehicles.clear()
        index=0
        for lane in self.incLanes:
            if (not "end" in lane) and (not "start" in lane):
                for vehicle in traci.inductionloop.getLastStepVehicleIDs("e1Detector"+lane):
                    self.temp_vehicle.append(vehicle)
                for vehicle in traci.edge.getLastStepVehicleIDs(lane):
                    self.onLaneVehicles.append(vehicle)
        self.temp_time = traci.simulation.getTime()
        if not self.temp_vehicle:
            # self.lead_time=traci.simulation.getTime()
            return
        if self.lead_vehicle==None or self.lead_vehicle not in self.onLaneVehicles:
            self.lead_time=traci.simulation.getTime()
            self.lead_vehicle=self.temp_vehicle[-1]
            return

        # time_interval = self.temp_time - self.lead_time
        if not traci.vehicle.getSpeed(self.lead_vehicle) or not (self.temp_vehicle[0]):
            return 
        if chase==1:
            lead_vehicle_speed = traci.vehicle.getSpeed(self.lead_vehicle)
            lead_vehicle_speed_arrive_time = self.lead_time + d1 / lead_vehicle_speed
            
            follower_vehicle_speed = traci.vehicle.getSpeed(self.temp_vehicle[0])
            follower_vehicle_assumed_arrive_time = self.temp_time + d1 / follower_vehicle_speed

            time_deduction = follower_vehicle_assumed_arrive_time - lead_vehicle_speed_arrive_time - collision_time_delay
            set_follower_speed = d1 / (d1 / follower_vehicle_speed - time_deduction)

            # limit the acceleration speed
            for vehicle in self.temp_vehicle:
                if set_follower_speed <= 40.0:
                    traci.vehicle.setSpeedMode(vehicle, 0)
                    traci.vehicle.setSpeed(vehicle, set_follower_speed)
                    set_follower_speed=d1 / (d1 / set_follower_speed - collision_time_delay)
                    index+=1
                else:
                    break
        
        self.lead_vehicle=self.temp_vehicle[-1]
        self.lead_time=self.temp_time
        
import numpy as np
from gym import spaces
class network:
    
    def __init__(self,path,ui,sumocfgPath,steptime=60):
        net=sumolib.net.readNet(path)
        self.junctions=[]
        self.lanes={}
        self.ui=ui
        self.sumocfgPath=sumocfgPath
        
        for node in net.getNodes():
            # if "junction" in node.getID() and node.getID()[9:] not in ["10","12"]:
            if "junction" in node.getID():
                self.junctions.append(junction(node.getID(),node.getIncoming(),node.getOutgoing(),node.getCoord()))
        self.sk=[0]*len(self.junctions)
        for edge in net.getEdges():
            # if "link" in edge.getID() and edge.getID()[4:] in ["1","3","5"]:
            if "link" in edge.getID():
                self.lanes[edge.getID()]=lane(edge.getID())

        self.action_space=spaces.Discrete(2)
        self.steptime=steptime
        self.observation_space=spaces.Box(np.array([0]*(len(self.lanes)-1)),np.array([1]*(len(self.lanes)-1)))
    
    # def step(self,params):
        
        
    #     for i in range(len(self.junctions)):
    #         curSK=self.sk[i]
            
    #         # calculation method
    #         if curSK > 40.0:
    #             if params == 0:
    #                 action = 0.0
    #                 return_reward = self.reward(action)[1]
    #             else:
    #                 action = 0.0
    #                 return_reward = -500.0
    #         else:
    #             if params == 0:
    #                 action = 0.0
    #                 return_reward = self.reward(action)[1]
    #             else:
    #                 action = curSK
    #                 return_reward = self.reward(action)[0]

    #         print("observation: ",curSK)
    #         print('action: ', action)
    #         print(self.reward(action))
    #         print('reward: ', return_reward)
    #         if not action==curSK:
    #             for junction in self.junctions:
    #                 if junction.ID=="junction0":
    #                     junction.coordinate(0)
    #         else:
    #             for junction in self.junctions:
    #                 if junction.ID=="junction0":
    #                     junction.coordinate(1)

    #     observation=self.get_observation()
    #     if traci.simulation.getTime()<86000:
    #         done=False
    #     else:
    #         done=True

    #     # prepare for next step while get the next sk
    #     # return_reward=0
    #     arrival=False
    #     while not arrival:
    #         traci.simulationStep()
    #         for vehicle in traci.vehicle.getIDList():
    #             vehicle_item_type = traci.vehicle.getTypeID(vehicle)
    #             if (vehicle_item_type == 'connected_pFollower' or vehicle_item_type == 'connected_pCatchup' or vehicle_item_type == 'connected_pCatchupFollower'):
    #                 traci.vehicle.setColor(vehicle,(0,255,0))
    #             else:
    #                 traci.vehicle.setColor(vehicle,(255,0,100))
    #         for i in range(len(self.junctions)): 
    #             self.junctions[i].restrictDrivingMode()
    #         for i in range(len(self.junctions)):
    #             if junction.ID=="junction0":
    #                 if junction.detectArrival():
    #                     self.sk=junction.getSK()
    #                     arrival=True
    #                     break
    #             # return_reward+=self.getTotalCost()
    #     return observation, -return_reward, done, {}

    def step(self,params):
        
        
        # simulation method
        if self.sk > 40.0:
            if params == 0:
                for junction in self.junctions:
                    if junction.ID=="junction0":
                        junction.coordinate(0)
        else:
            if params == 0:
                for junction in self.junctions:
                    if junction.ID=="junction0":
                        junction.coordinate(0)
            else:
                for junction in self.junctions:
                    if junction.ID=="junction0":
                        junction.coordinate(1)
        print("observation: ",self.sk)
        
        observation=self.get_observation()
        if traci.simulation.getTime()<86000:
            done=False
        else:
            done=True

        # prepare for next step while get the next sk
        # return_reward=0
        arrival=False
        return_reward=0
        for junction in self.junctions:
            if junction.ID=="junction0":
                curSK=junction.getSK()
        while not arrival:
            traci.simulationStep()
            for vehicle in traci.vehicle.getIDList():
                vehicle_item_type = traci.vehicle.getTypeID(vehicle)
                if (vehicle_item_type == 'connected_pFollower' or vehicle_item_type == 'connected_pCatchup' or vehicle_item_type == 'connected_pCatchupFollower'):
                    traci.vehicle.setColor(vehicle,(0,255,0))
                else:
                    traci.vehicle.setColor(vehicle,(255,0,100))
            for i in range(len(self.junctions)): 
                self.junctions[i].restrictDrivingMode()
            for junction in self.junctions:
                if junction.ID=="junction0":
                    if junction.detectArrival():
                        self.sk=junction.getSK()-curSK
                        arrival=True
                        break
                return_reward+=self.getTotalCost()[0]
        print('reward: ', return_reward)
        return observation, -return_reward, done, {}
    
    def render(self, mode='human', close=False):
        return

    def get_observation(self):
        return self.sk

    def reset(self):
        traci.close()
        gr.generate_routefile()
        traci.start([sumolib.checkBinary(self.ui), '-c', os.path.join(self.sumocfgPath)])
        simpla.load("data/simpla.cfg.xml")
        for junction in self.junctions:
            for lane in self.lanes:
                self.lanes[lane].reset()
        for i in range(1200):
            traci.simulationStep()
        arrival=False
        while not arrival:
            traci.simulationStep()
            for vehicle in traci.vehicle.getIDList():
                vehicle_item_type = traci.vehicle.getTypeID(vehicle)
                if (vehicle_item_type == 'connected_pFollower' or vehicle_item_type == 'connected_pCatchup' or vehicle_item_type == 'connected_pCatchupFollower'):
                    traci.vehicle.setColor(vehicle,(0,255,0))
                else:
                    traci.vehicle.setColor(vehicle,(255,0,100))
            for i in range(len(self.junctions)): 
                self.junctions[i].restrictDrivingMode()
            for junction in self.junctions:
                if junction.ID=="junction0":
                    if junction.detectArrival():
                        arrival=True
                        self.sk=junction.getSK()
                        junction.coordinate(0)
                        break
        arrival=False
        while not arrival:
            traci.simulationStep()
            for vehicle in traci.vehicle.getIDList():
                vehicle_item_type = traci.vehicle.getTypeID(vehicle)
                if (vehicle_item_type == 'connected_pFollower' or vehicle_item_type == 'connected_pCatchup' or vehicle_item_type == 'connected_pCatchupFollower'):
                    traci.vehicle.setColor(vehicle,(0,255,0))
                else:
                    traci.vehicle.setColor(vehicle,(255,0,100))
            for i in range(len(self.junctions)): 
                self.junctions[i].restrictDrivingMode()
            for junction in self.junctions:
                if junction.ID=="junction0":
                    if junction.detectArrival():
                        arrival=True
                        self.sk=junction.getSK()
                        break
        return self.get_observation()

    def close(self):
        traci.close()


    def getTotalCost(self):
        total_cost=0
        total_fuel=0
        total_time=0
        for junction in self.junctions:
            data=junction.getTotalCost()
            total_cost+=data[0]
            total_fuel+=data[1]
            total_time+=data[2]
        return total_cost,total_fuel,total_time


    def reward(self, sk_action):
        reward_catch_up = alpha * d1 * v**2 + eta * k * d2 - alpha * d1 * (d1 / (d1 / v - sk_action))**2
        reward_catch_up = reward_catch_up * w_2 + sk_action * w_1

        reward_not_catch_up = alpha * d1 * v**2 - alpha * d1 * (d1 / (d1 / v - sk_action))**2
        reward_not_catch_up = reward_not_catch_up * w_2 + sk_action * w_1
        print("insideReward",[reward_catch_up, reward_not_catch_up])
        return [reward_catch_up, reward_not_catch_up]

    
    # def action(self,params):
    #     traci.simulationStep()
    #     for vehicle in traci.vehicle.getIDList():
    #         vehicle_item_type = traci.vehicle.getTypeID(vehicle)
    #         if (vehicle_item_type == 'connected_pFollower' or vehicle_item_type == 'connected_pCatchup' or vehicle_item_type == 'connected_pCatchupFollower'):
    #             traci.vehicle.setColor(vehicle,(0,255,0))
    #         else:
    #             traci.vehicle.setColor(vehicle,(255,0,100))
    #     for i in range(len(self.junctions)): 
            
            
    #         # print(self.junctions[i].ID,":",threshold,C)
    #         self.junctions[i].restrictDrivingMode()
    #         if self.junctions[i].ID=="junction0":
    #             toUpdate=self.junctions[i].detectArrival()
    #             if toUpdate:
    #                 self.junctions[i].coordinate(params)
    #                 for lane in toUpdate:
    #                     if lane in self.lanes:
    #                         self.lanes[lane].updateFlow()
    
    def getFlow(self,lane):
        return self.lanes[lane].flow



    
try:
    sys.path.append(os.path.join(os.path.dirname(
    __file__), '..', '..', '..', '..', "tools"))
    sys.path.append(os.path.join(os.environ.get("SUMO_HOME", os.path.join(
    os.path.dirname(__file__), "..", "..", "..")), "tools")) 
except ImportError:
    sys.exit("please declare environment variable 'SUMO_HOME' as the root directory of your sumo installation (it should contain folders 'bin', 'tools' and 'docs')")



# choose whether to use GUI or not
netconvertBinary = checkBinary('netconvert')
sumoBinary = checkBinary('sumo-gui')


# begin the simulation

# generate the final SUMO file, include net file and vehicle file
traci.start([sumoBinary, '-c', os.path.join("Nguyen-Dupuis/merge.sumocfg")])

simpla.load("data/simpla.cfg.xml")
mgr=simpla._mgr
env=network("Nguyen-Dupuis/merge.net.xml","sumo-gui","Nguyen-Dupuis/merge.sumocfg")
# env=pyENV.network("ND/newND.net.xml","sumo","ND/test.sumocfg")
totalcost=0
totalfuel=0
totaltime=0
for k in range(75000):
    env.step(1)
    data=env.getTotalCost()
    totalcost+=data[0]
    totalfuel+=data[1]
    totaltime+=data[2]
traci.close()
print(totalcost,totalfuel,totaltime)