# Import python modules
import datetime
import time
import json
from threading import Thread
import sys
import traceback
import logging

from sseclient import SSEClient as EventSource

# Import core modules
from core import config_loader as cfgl
from core import rule_executor
from core import yapi
from core import path
from core import timelib

api = yapi.MWAPI
logger = logging.getLogger("infolog")

f_dict = open(path.main()+"core/dict.json")
dictionary = json.load(f_dict)

def shouldCheck(rev):
	# Check should revision to be checked at all
	revs = api.getRevision([rev["revision"]["new"]])

	if "badrevids" in revs["query"]:
		return False

	if api.stabilized(rev["title"]):
		return False

	if not api.reviewed(rev["title"]):
		return False

	return True

# Object that sends kill signal to ConfigUpdater thread
class Killer:
	kill = False

# Updates config when changed every 30 seconds
class ConfigUpdate(Thread):

	killer = None

	def __init__(self, killer):
		self.killer = killer
		super(ConfigUpdate, self).__init__()

	def run(self):
		if cfgl.cur_conf["core"]["config_mode"] == "online":
			logger.info("online config mode enabled")
		else:
			logger.info("local config mode enabled")

		uf = 30
		times = uf
		while True:
			if self.killer.kill:
				return
			if times >= uf:
				times = 0
				if cfgl.cur_conf["core"]["config_mode"] == "online":
					cfgl.checkForOnlineUpdate()
				else:
					cfgl.checkForLocalUpdate()

			if self.killer.kill:
				return

			time.sleep(0.5)
			times += 0.5

class Stabilizer(Thread):

	killer = None

	def __init__(self, killer, pending, rev, expiry):
		self.killer = killer
		self.pending = pending
		self.rev = rev
		self.expiry = expiry
		super(Stabilizer, self).__init__()

	def stabilize(self):
		if not cfgl.cur_conf["core"]["test"] and not cfgl.cur_conf["core"]["test"]:
			# Calculate expiry
			dtexpiry = datetime.datetime.utcnow() + datetime.timedelta(hours=self.expiry, minutes=0, seconds=0)
			# Set reason
			revlink = "[[Special:Diff/"+str(self.rev["revision"]["new"])+"|"+str(self.rev["revision"]["new"])+"]]"
			reason = dictionary[cfgl.cur_conf["core"]["lang"]]["reasons"]["YV1"] % revlink

			# Stabilize
			api.stabilize(self.rev["title"], reason, expiry=timelib.toString(dtexpiry))

			return True

		return False

	def run(self):
		times = 0
		while times < cfgl.cur_conf["core"]["s_delay"]:
			if self.killer.kill:
				return False
			time.sleep(0.5)
			times += 0.5

		if shouldCheck(self.rev):
			self.pending.remove(self.rev["title"])
			self.stabilize()
		return True

class Worker:
	r_exec = None
	killer = None
	cf_updater = None
	pending = []
	tries = 0

	def __init__(self):
		self.pending = []
		self.r_exec = rule_executor.Executor()
		# Init ConfigUpdater
		self.killer = Killer()
		self.cf_updater = ConfigUpdate(self.killer)
		self.cf_updater.start()
		tries = 0

	def run(self):
		try:
			wiki = cfgl.cur_conf["core"]["lang"]+"wiki"
			# Event stream
			for event in EventSource(cfgl.cur_conf["core"]["stream_url"]):
				# Filter event stream
				if event.event == 'message':
					try:
						change = json.loads(event.data)
					except ValueError:
						continue

					if change["wiki"] == wiki and change["type"] == "edit" and change["namespace"] in cfgl.cur_conf["core"]["namespaces"]:
						if self.tries != 0:
							self.tries = 0
						# Check should revision to be checked at all
						if shouldCheck(change) and change["title"] not in self.pending:
							expiry = self.r_exec.shouldStabilize(change)
							if expiry and not cfgl.cur_conf["core"]["test"] and change["title"] not in self.pending:
								#self.stabilize(change, expiry)
								self.pending.append(change["title"])
								stabilizer = Stabilizer(self.killer, self.pending, change, expiry)
								stabilizer.start()

		except KeyboardInterrupt:
			logger.info("terminating stabilizer...")
			self.killer.kill = True
			self.cf_updater.join()
		except ConnectionResetError:
			if self.tries == 5:
				logger.error("giving up")
				sys.exit(1)
			logger.error("error: connection error\n trying to reconnect...")
			self.tries += 1
			self.run()
		except:
			logger.error("error: faced unexcepted error check crash report")
			logger.critical(traceback.format_exc())
			logger.info("terminating threads")
			sys.exit(1)
