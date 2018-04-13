#!/usr/bin/python3

import paho.mqtt.client as mqtt
import telnetlib
import json
import copy
import time
import argparse

class Snapcast2MQTT:
    def __init__(self, brokerHost, brokerPort, rootTopic, snapcastHost, snapcastPort):
        self._rootTopic = rootTopic
        self._mqttClient = mqtt.Client()
        self._mqttClient.on_connect = self._mqtt_on_connect
        self._mqttClient.on_message = self._mqtt_on_message
        self._brokerHost = brokerHost
        self._brokerPort = brokerPort
        self._snapcastHost = snapcastHost
        self._snapcastPort = snapcastPort
        self._snapcast = telnetlib.Telnet()
        self._methodDispatcher = {
            "Client.OnVolumeChanged": self._clientVolumeChanged,
            "Client.SetVolume": self._clientVolumeChanged
        }
        self._topicDispatcher = {
            "client" : self._clientTopicDispatcher
        }
        self._clientDispatcher = {
            "mute": self._clientMute,
            "status": self._clientStatus
        }
        self._lastId = 0
        self._quedRequests = {}

    def _mqtt_on_connect(self, client, userdata, flags, rc):
        print("Connected with result code " + str(rc))
        self._mqttClient.subscribe(self._rootTopic + "in/#")

    def _mqtt_on_message(self, client, obj, msg):
        payload = msg.payload.decode("utf-8")
        print ('received topic: %s. payload: %s' % (msg.topic, payload))
        parts = msg.topic.split("/")[1:]
        method = self._topicDispatcher.get(parts[0], lambda payload, *parts: None)
        request = method(payload, *parts[1:])
        self._snapcast.write(request)
        print ('sent request: %s' % request.decode('utf-8').strip())

    def _clientTopicDispatcher(self, payload, clientId, command, *parts):
        method = self._clientDispatcher.get(command, lambda payload, clientId, *parts: None)
        return method(payload, clientId, *parts)

    def _clientMute(self, payload, clientId):
        data = {"method": "Client.SetVolume",
                "params": {"id": clientId, "volume": {"muted": bool(int(payload)), "percent": 100}}}
        def responseMethod(response):
            self._handleNotification(data)

        return self._makeRequest({"method": "Client.SetVolume",
                                  "params": {"id": clientId, "volume": {"muted": bool(int(payload)), "percent": 100}}},
                                 responseMethod)

    def _clientStatus(self, payload, clientId):
        def responseMethod(response):
            clientId = response["result"]["client"]["id"]
            mute = response["result"]["client"]["config"]["volume"]["muted"]
            volumeData = {"method": "Client.OnVolumeChanged",
                          "params": {"id": clientId, "volume": {"muted": mute, "percent": 100}}}
            self._handleNotification(volumeData)
        return self._makeRequest({"method": "Client.GetStatus",
                                  "params": {"id": clientId}},
                                 responseMethod)

    def _makeRequest(self, data, responsMethod):
        self._lastId += 1
        data.update({"id": self._lastId, "jsonrpc": "2.0"})
        self._quedRequests[self._lastId] = responsMethod
        return json.dumps(data).encode('utf-8') + b"\r\n"

    def _handleResponse(self, response):
        requestId = response["id"]
        if requestId in self._quedRequests:
            method = self._quedRequests.pop(requestId)
            if "error" not in response:
                method(response)

    def _makeTopic(self, *parts):
        return "/".join([self._rootTopic+'out'] + list(parts))

    def _clientVolumeChanged(self, params):
        return self._makeTopic("client", params["id"], "mute"), str(int(params["volume"]["muted"]))

    def _convertToTopic(self, method, params):
        method = self._methodDispatcher.get(method, lambda params: (None, None))
        return method(params)

    def _handleNotification(self, notification):
        topic, payload = self._convertToTopic(notification["method"], notification["params"])
        if topic is None:
            print('unknown notification')
        else:
            print('sending topic: %s. payload: %s' % (topic, payload))
            self._mqttClient.publish(topic, payload)

    def _telnetLoop(self):
        print('connecting to [%s:%s]' % (self._snapcastHost, self._snapcastPort))
        self._snapcast.open(self._snapcastHost, self._snapcastPort)
        while True:
            result = self._snapcast.read_until(b"\n")
            print('received response %s' % result.decode("utf-8").strip())
            notification = json.loads(result.decode("utf-8"))
            if "id" in notification:
                self._handleResponse(notification)
            else:
                self._handleNotification(notification)

    def run(self):
        self._mqttClient.connect_async(self._brokerHost, self._brokerPort)
        self._mqttClient.loop_start()
        while True:
            try:
                self._telnetLoop()
            except (EOFError, ConnectionRefusedError):
                print('lost connection')
                time.sleep(5)

    def stop(self):
        self._mqttClient.loop_stop()

parser = argparse.ArgumentParser()
parser.add_argument('--broker-host', default='localhost')
parser.add_argument('--broker-port', type=int, default=1883)
parser.add_argument('--snapcast-host', default='localhost')
parser.add_argument('--snapcast-port', type=int, default=1705)
args = parser.parse_args()

rootTopic = 'snapcast'

snapcast2Mqtt = Snapcast2MQTT(args.broker_host, args.broker_port, rootTopic, args.snapcast_host, args.snapcast_port)
snapcast2Mqtt.run()
