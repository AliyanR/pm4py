'''
    PM4Py – A Process Mining Library for Python
Copyright (C) 2024 Process Intelligence Solutions UG (haftungsbeschränkt)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see this software project's root or
visit <https://www.gnu.org/licenses/>.

Website: https://processintelligence.solutions
Contact: info@processintelligence.solutions
'''
from copy import copy

from pm4py.algo.conformance.alignments.petri_net import variants
from pm4py.objects.petri_net.utils import align_utils, check_soundness
from pm4py.objects.conversion.log import converter as log_converter
from pm4py.util.xes_constants import DEFAULT_NAME_KEY, DEFAULT_TRACEID_KEY
from pm4py.objects.log.obj import Trace, Event
import time
from pm4py.util.lp import solver
from pm4py.util import exec_utils
from enum import Enum
import sys
from pm4py.util.constants import (
    PARAMETER_CONSTANT_ACTIVITY_KEY,
    PARAMETER_CONSTANT_CASEID_KEY,
    CASE_CONCEPT_NAME,
)
import importlib.util
from typing import Optional, Dict, Any, Union
from pm4py.objects.log.obj import EventLog, EventStream, Trace
from pm4py.objects.petri_net.obj import PetriNet, Marking
from pm4py.util import typing, constants, pandas_utils
import pandas as pd


class Variants(Enum):
    VERSION_STATE_EQUATION_A_STAR = variants.state_equation_a_star
    VERSION_DIJKSTRA_NO_HEURISTICS = variants.dijkstra_no_heuristics
    VERSION_DIJKSTRA_LESS_MEMORY = variants.dijkstra_less_memory
    VERSION_DISCOUNTED_A_STAR = variants.discounted_a_star

class Parameters(Enum):
    PARAM_TRACE_COST_FUNCTION = "trace_cost_function"
  
