#!/usr/bin/env python
import rospy
import numpy as np
import math
from math import pi
from geometry_msgs.msg import Twist, Point, Pose
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from std_srvs.srv import Empty
from std_msgs.msg import Bool, Float64MultiArray
from tf.transformations import euler_from_quaternion, quaternion_from_euler
from datetime import datetime

# # pathfollowing
# world = False
# if world:
#     from respawnGoal_custom_worlds import Respawn
# else:
#     from respawnGoal_2D import Respawn
# import copy
# target_not_movable = False

# Navegation
world = True
from respawnGoal_2D import Respawn
import copy
target_not_movable = True

class Env():
    def __init__(self, action_dim=2):
        global target_not_movable
        self.goal_x = 0
        self.goal_y = 0
        self.heading = 0
        self.initGoal = True
        self.get_goalbox = False
        self.position = Pose()
        self.pub_cmd_vel = rospy.Publisher('/hydrone_aerial_underwater/cmd_vel', Twist, queue_size=5)
        self.sub_odom = rospy.Subscriber('/hydrone_aerial_underwater/ground_truth/odometry', Odometry, self.getOdometry)
        self.reset_proxy = rospy.ServiceProxy('gazebo/reset_world', Empty)
        self.pub_pose = rospy.Publisher("/hydrone_aerial_underwater/ground_truth/pose", Pose, queue_size=5)
        self.pub_end = rospy.Publisher("/hydrone_aerial_underwater/end_testing", Bool, queue_size=5)
        self.pub_reward = rospy.Publisher("/hydrone_aerial_underwater/rewarded", Bool, queue_size=5)
        self.eps_to_test = rospy.get_param('~num_eps_test')
        self.counter_eps = 0
        self.unpause_proxy = rospy.ServiceProxy('gazebo/unpause_physics', Empty)
        self.pause_proxy = rospy.ServiceProxy('gazebo/pause_physics', Empty)
        self.respawn_goal = Respawn()
        self.past_distance = 0.
        self.arriving_distance = rospy.get_param('~arriving_distance')
        self.evaluating = rospy.get_param('~test_param')
        self.eval_path = rospy.get_param('~eval_path')
        if (self.eval_path):
            target_not_movable = False
        else:
            target_not_movable = True
        self.stopped = 0
        self.action_dim = action_dim
        self.last_time = datetime.now()

        self.hardstep = 0
        #Keys CTRL + c will stop script
        rospy.on_shutdown(self.shutdown)

    def shutdown(self):
        #you can stop turtlebot by publishing an empty Twist
        #message
        rospy.loginfo("Stopping Simulation")
        self.pub_cmd_vel.publish(Twist())
        rospy.sleep(1)

    def getGoalDistace(self):
        goal_distance = math.sqrt((self.goal_x - self.position.x)**2 + (self.goal_y - self.position.y)**2)
        self.past_distance = goal_distance

        return goal_distance

    def getOdometry(self, odom):
        self.past_position = copy.deepcopy(self.position)
        self.position = odom.pose.pose.position
        orientation = odom.pose.pose.orientation
        orientation_list = [orientation.x, orientation.y, orientation.z, orientation.w]
        _, _, yaw = euler_from_quaternion(orientation_list)

        goal_angle = math.atan2(self.goal_y - self.position.y, self.goal_x - self.position.x)

        #print 'yaw', yaw
        #print 'gA', goal_angle

        heading = goal_angle - yaw
        #print 'heading', heading
        if heading > pi:
            heading -= 2 * pi

        elif heading < -pi:
            heading += 2 * pi

        self.heading = round(heading, 3)

    def getState(self, scan_lidar, scan_sonar, past_action):
        scan_range = []
        heading = self.heading
        min_range = 0.5
        done = False

        if (self.position.z < 0.0):
            for i in range(len(scan_sonar.data)):
                if (i%(len(scan_sonar.data)/19) == 0):
                    if scan_sonar.data[i] == float('Inf'):
                        scan_range.append(20.0)
                    elif np.isnan(scan_sonar.data[i]):
                        scan_range.append(0)
                    else:
                        scan_range.append(scan_sonar.data[i])
        else:
            for i in range(len(scan_lidar.ranges)):
                if scan_lidar.ranges[i] == float('Inf'):
                    scan_range.append(20.0)
                elif np.isnan(scan_lidar.ranges[i]):
                    scan_range.append(0)
                else:
                    scan_range.append(scan_lidar.ranges[i])

        # print(len(scan_range))
        # print(min(scan_range))

        # if min_range > min(scan_range) > 0.0:
        #     done = True
        if min_range > min(scan_range) or self.position.z < -4.8 or self.position.z > 0.2:
            # print(scan_range)
            done = True

        for pa in past_action:
            scan_range.append(pa)

        current_distance = round(math.hypot(self.goal_x - self.position.x, self.goal_y - self.position.y),2)
        if current_distance < self.arriving_distance:
            self.get_goalbox = True

        # print(heading, current_distance)

        return scan_range + [heading, current_distance], done

    def setReward(self, state, done):
        reward = 0

        if done:
            rospy.loginfo("Collision!!")
            # reward = -550.
            reward = -10.
            self.pub_cmd_vel.publish(Twist())

            self.respawn_goal.counter = 0

        if self.get_goalbox:
            rospy.loginfo("Goal!!")
            # reward = 500.
            reward = 100.
            self.pub_cmd_vel.publish(Twist())
            if world and target_not_movable:
                self.reset()
            self.goal_x, self.goal_y = self.respawn_goal.getPosition(True, delete=True)
            self.goal_distance = self.getGoalDistace()
            self.get_goalbox = False

        if (reward == 100 and self.evaluating==True and self.eval_path==False):
            self.pub_reward.publish(True)

        if (reward == 100 and self.evaluating==True and self.eval_path==True and (self.respawn_goal.counter%(len(self.respawn_goal.goal_x_list)+1))==0):
            self.pub_reward.publish(True)
            self.respawn_goal.counter = 0
            self.reset()

        if (self.hardstep == 1000 and self.evaluating==True and self.eval_path==True):
            self.respawn_goal.counter = 0
            self.reset()
        # else:
        #     self.pub_reward.publish(False)

        return reward, done

    def step(self, action, past_action):
        linear_vel_x = action[0]
        angular_vel_z = action[1]
        # angular_vel_z = action[2]

        vel_cmd = Twist()
        vel_cmd.linear.x = linear_vel_x
        # vel_cmd.linear.y = linear_vel_y
        vel_cmd.angular.z = angular_vel_z

        self.pub_cmd_vel.publish(vel_cmd)

        self.hardstep += 1

        data_sonar = None
        while data_sonar is None:
            try:
                data_sonar = rospy.wait_for_message('/sonar_distance', Float64MultiArray, timeout=5)
            except:
                pass

        data_lidar = None
        while data_lidar is None:
            try:
                data_lidar = rospy.wait_for_message('/hydrone_aerial_underwater/scan', LaserScan, timeout=5)
            except:
                pass

        state, done = self.getState(data_lidar, data_sonar, past_action)
        reward, done = self.setReward(state, done)

        return np.asarray(state), reward, done

    def reset(self):
        #print('aqui2_____________---')
        rospy.wait_for_service('gazebo/reset_simulation')
        try:
            self.reset_proxy()
        except (rospy.ServiceException) as e:
            print("gazebo/reset_simulation service call failed")

        data_sonar = None
        while data_sonar is None:
            try:
                data_sonar = rospy.wait_for_message('/sonar_distance', Float64MultiArray, timeout=5)
            except:
                pass

        data_lidar = None
        while data_lidar is None:
            try:
                data_lidar = rospy.wait_for_message('/hydrone_aerial_underwater/scan', LaserScan, timeout=5)
            except:
                pass

        if self.initGoal:
            self.goal_x, self.goal_y = self.respawn_goal.getPosition()
            self.initGoal = False
        else:
            self.goal_x, self.goal_y = self.respawn_goal.getPosition(True, delete=True)

        # publish the episode time
        timer = Twist()
        timer.linear.y = (datetime.now() - self.last_time).total_seconds()
        self.pub_cmd_vel.publish(timer)
        self.last_time = datetime.now()

        if((self.counter_eps == self.eps_to_test) and self.evaluating == True):
            self.pub_end.publish(False)
            rospy.signal_shutdown("end_test")

        self.counter_eps += 1

        self.hardstep = 0

        if (self.evaluating):
            rospy.loginfo("Test number: %s", str(self.counter_eps))

        # pose_reset = Pose()
        # pose_reset.position.x = -100.0
        # self.pub_pose.publish(pose_reset)

        self.goal_distance = self.getGoalDistace()
        # state, _ = self.getState(data, [0.,0., 0.0])
        state, _ = self.getState(data_lidar, data_sonar, [0]*self.action_dim)

        return np.asarray(state)
