"""
    PM4Py – A Process Mining Library for Python
Energy-Optimized Alignment mit Prefix-Cache (Trie)

Copyright (C) 2024 Process Intelligence Solutions UG (haftungsbeschränkt)

Lizenz: GNU Affero General Public License v3 oder später.
"""

import time
import sys
import heapq
from copy import copy
from enum import Enum
from typing import Optional, Dict, Any, Union
from pm4py.objects.petri_net.utils import align_utils
from pm4py.objects.log import obj as log_implementation
from pm4py.util.constants import PARAMETER_CONSTANT_ACTIVITY_KEY
from pm4py.util.xes_constants import DEFAULT_NAME_KEY
from pm4py.util import exec_utils, variants_util
from pm4py.objects.petri_net.semantics import enabled_transitions
from pm4py.objects.log.obj import Trace
from pm4py.objects.petri_net.obj import PetriNet, Marking
from pm4py.util import typing


# ========================
# Parameter / Konstanten
# ========================
class Parameters(Enum):
    PARAM_TRACE_COST_FUNCTION = "trace_cost_function"
    PARAM_MODEL_COST_FUNCTION = "model_cost_function"
    PARAM_STD_SYNC_COST = "std_sync_cost"
    PARAM_MAX_ALIGN_TIME_TRACE = "max_align_time_trace"
    PARAM_MAX_ALIGN_TIME = "max_align_time"
    PARAMETER_VARIANT_DELIMITER = "variant_delimiter"
    PARAM_ALIGNMENT_RESULT_IS_SYNC_PROD_AWARE = "ret_tuple_as_trans_desc"
    ACTIVITY_KEY = PARAMETER_CONSTANT_ACTIVITY_KEY

    # NEU: Energieoptimierung
    ENABLE_PREFIX_CACHE = "enable_prefix_cache"
    PREFIX_CACHE_MAX_NODES = "prefix_cache_max_nodes"
    PREFIX_CACHE_STATS = "prefix_cache_stats"


PLACES_DICT = "places_dict"
INV_TRANS_DICT = "inv_trans_dict"
LABELS_DICT = "labels_dict"
TRANS_LABELS_DICT = "trans_labels_dict"
TRANS_PRE_DICT = "trans_pre_dict"
TRANS_POST_DICT = "trans_post_dict"
TRANSF_IM = "transf_im"
TRANSF_FM = "transf_fm"
TRANSF_MODEL_COST_FUNCTION = "transf_model_cost_function"
TRANSF_TRACE = "transf_trace"
TRACE_COST_FUNCTION = "trace_cost_function"
INV_TRACE_LABELS_DICT = "inv_trace_labels_dict"

IS_SYNC_MOVE = 0
IS_LOG_MOVE = 1
IS_MODEL_MOVE = 2

POSITION_TOTAL_COST = 0
POSITION_INDEX = 1
POSITION_TYPE_MOVE = 2
POSITION_ALIGN_LENGTH = 3
POSITION_STATES_COUNT = 4
POSITION_PARENT_STATE = 5
POSITION_MARKING = 6
POSITION_EN_T = 7


# ========================
# Prefix-Cache (Trie)
# ========================
class _TrieNode:
    __slots__ = ("children", "marking", "cost", "idx")

    def __init__(self):
        self.children = {}
        self.marking = None
        self.cost = None
        self.idx = 0


class AlignmentTrie:
    def __init__(self, max_nodes=200_000):
        self.root = _TrieNode()
        self.max_nodes = max_nodes
        self.nodes = 1
        self.hits = 0
        self.inserts = 0

    def lookup_longest(self, labels_seq):
        node = self.root
        best = (None, 0)
        for i, lab in enumerate(labels_seq):
            nxt = node.children.get(lab)
            if not nxt:
                break
            node = nxt
            if node.marking is not None:
                best = (node, i + 1)
        if best[0] is not None:
            self.hits += 1
        return best

    def insert_state(self, labels_prefix, marking, cost, consumed_len):
        if self.nodes >= self.max_nodes:
            return
        node = self.root
        for lab in labels_prefix:
            nxt = node.children.get(lab)
            if nxt is None:
                nxt = _TrieNode()
                node.children[lab] = nxt
                self.nodes += 1
                if self.nodes >= self.max_nodes:
                    break
            node = nxt
        if node.marking is None or (cost is not None and cost < node.cost):
            node.marking = marking
            node.cost = cost
            node.idx = consumed_len
            self.inserts += 1


_ALIGN_TRIE = AlignmentTrie()
_PREFIX_CACHE_ENABLED = False


# ========================
# Öffentliche API
# ========================
def get_best_worst_cost(petri_net, initial_marking, final_marking, parameters=None):
    if parameters is None:
        parameters = {}
    trace = log_implementation.Trace()
    best_worst = apply(trace, petri_net, initial_marking, final_marking, parameters=parameters)
    if best_worst is not None:
        return best_worst["cost"]
    return None


def apply_from_variants_list_petri_string(var_list, petri_net_string, parameters=None):
    if parameters is None:
        parameters = {}
    from pm4py.objects.petri_net.importer.variants import pnml as petri_importer
    petri_net, im, fm = petri_importer.import_petri_from_string(petri_net_string)
    return apply_from_variants_list(var_list, petri_net, im, fm, parameters=parameters)


def apply_from_variants_list(var_list, petri_net, im, fm, parameters=None):
    if parameters is None:
        parameters = {}
    start_time = time.time()
    max_align_time = exec_utils.get_param_value(Parameters.PARAM_MAX_ALIGN_TIME, parameters, sys.maxsize)
    max_align_time_trace = exec_utils.get_param_value(Parameters.PARAM_MAX_ALIGN_TIME_TRACE, parameters, sys.maxsize)
    dictio_alignments = {}
    for varitem in var_list:
        this_max_align_time = min(
            max_align_time_trace, (max_align_time - (time.time() - start_time)) * 0.5
        )
        variant = varitem[0]
        parameters[Parameters.PARAM_MAX_ALIGN_TIME_TRACE] = this_max_align_time
        dictio_alignments[variant] = apply_from_variant(variant, petri_net, im, fm, parameters=parameters)
    return dictio_alignments


def apply_from_variant(variant, petri_net, im, fm, parameters=None):
    if parameters is None:
        parameters = {}
    trace = variants_util.variant_to_trace(variant, parameters=parameters)
    return apply(trace, petri_net, im, fm, parameters=parameters)


# ========================
# Transformationen
# ========================
def __transform_model_to_mem_efficient_structure(net, im, fm, trace, parameters=None):
    if parameters is None:
        parameters = {}
    activity_key = exec_utils.get_param_value(Parameters.ACTIVITY_KEY, parameters, DEFAULT_NAME_KEY)
    labels = sorted(list(set(x[activity_key] for x in trace)))
    model_cost_function = exec_utils.get_param_value(Parameters.PARAM_MODEL_COST_FUNCTION, parameters, None)

    if model_cost_function is None:
        model_cost_function = {}
        for t in net.transitions:
            if t.label is not None:
                model_cost_function[t] = align_utils.STD_MODEL_LOG_MOVE_COST
            else:
                preset_t = Marking({a.source: a.weight for a in t.in_arcs})
                en_t = enabled_transitions(net, preset_t)
                vis_t_trace = [t for t in en_t if t.label in labels]
                model_cost_function[t] = 0 if len(vis_t_trace) == 0 else align_utils.STD_TAU_COST

    places_dict = {place: index for index, place in enumerate(net.places)}
    trans_dict = {trans: index for index, trans in enumerate(net.transitions)}
    labels = sorted(list(set(t.label for t in net.transitions if t.label is not None)))
    labels_dict = {labels[i]: i for i in range(len(labels))}
    trans_labels_dict = {trans_dict[t]: (labels_dict[t.label] if t.label is not None else None)
                         for t in net.transitions}
    trans_pre_dict = {trans_dict[t]: {places_dict[x.source]: x.weight for x in t.in_arcs} for t in net.transitions}
    trans_post_dict = {trans_dict[t]: {places_dict[x.target]: x.weight for x in t.out_arcs} for t in net.transitions}
    transf_im = {places_dict[p]: im[p] for p in im}
    transf_fm = {places_dict[p]: fm[p] for p in fm}
    transf_model_cost_function = {trans_dict[t]: model_cost_function[t] for t in net.transitions}
    inv_trans_dict = {y: x for x, y in trans_dict.items()}

    return {
        PLACES_DICT: places_dict,
        INV_TRANS_DICT: inv_trans_dict,
        LABELS_DICT: labels_dict,
        TRANS_LABELS_DICT: trans_labels_dict,
        TRANS_PRE_DICT: trans_pre_dict,
        TRANS_POST_DICT: trans_post_dict,
        TRANSF_IM: transf_im,
        TRANSF_FM: transf_fm,
        TRANSF_MODEL_COST_FUNCTION: transf_model_cost_function,
    }


def __transform_trace_to_mem_efficient_structure(trace, model_struct, parameters=None):
    if parameters is None:
        parameters = {}
    activity_key = exec_utils.get_param_value(Parameters.ACTIVITY_KEY, parameters, DEFAULT_NAME_KEY)
    trace_cost_function = exec_utils.get_param_value(Parameters.PARAM_TRACE_COST_FUNCTION, parameters, None)
    if trace_cost_function is None:
        trace_cost_function = {i: align_utils.STD_MODEL_LOG_MOVE_COST for i in range(len(trace))}
    labels = sorted(list(set(x[activity_key] for x in trace)))
    labels_dict = copy(model_struct[LABELS_DICT])
    for l in labels:
        if l not in labels_dict:
            labels_dict[l] = len(labels_dict)
    transf_trace = [labels_dict[x[activity_key]] for x in trace]
    inv_trace_labels_dict = {y: x for x, y in labels_dict.items()}
    return {TRANSF_TRACE: transf_trace, TRACE_COST_FUNCTION: trace_cost_function,
            INV_TRACE_LABELS_DICT: inv_trace_labels_dict}


# ========================
# Apply mit Cache
# ========================
def apply(trace: Trace, net: PetriNet, im: Marking, fm: Marking,
          parameters: Optional[Dict[Union[str, Parameters], Any]] = None) -> typing.AlignmentResult:
    global _PREFIX_CACHE_ENABLED
    if parameters is None:
        parameters = {}
    model_struct = __transform_model_to_mem_efficient_structure(net, im, fm, trace, parameters=parameters)
    trace_struct = __transform_trace_to_mem_efficient_structure(trace, model_struct, parameters=parameters)
    sync_cost = exec_utils.get_param_value(Parameters.PARAM_STD_SYNC_COST, parameters, align_utils.STD_SYNC_COST)
    max_align_time_trace = exec_utils.get_param_value(Parameters.PARAM_MAX_ALIGN_TIME_TRACE, parameters, sys.maxsize)
    ret_tuple_as_trans_desc = exec_utils.get_param_value(
        Parameters.PARAM_ALIGNMENT_RESULT_IS_SYNC_PROD_AWARE, parameters, False)

    _PREFIX_CACHE_ENABLED = exec_utils.get_param_value(Parameters.ENABLE_PREFIX_CACHE, parameters, False)
    if _PREFIX_CACHE_ENABLED:
        max_nodes = exec_utils.get_param_value(Parameters.PREFIX_CACHE_MAX_NODES, parameters, 200_000)
        _ALIGN_TRIE.max_nodes = max_nodes

    seed = None
    if _PREFIX_CACHE_ENABLED:
        labels = trace_struct[TRANSF_TRACE]
        node, consumed = _ALIGN_TRIE.lookup_longest(labels)
        if node is not None and node.marking is not None:
            seed = (node.cost or 0.0, consumed, node.marking)

    result = __dijkstra(model_struct, trace_struct,
                        sync_cost=sync_cost,
                        max_align_time_trace=max_align_time_trace,
                        ret_tuple_as_trans_desc=ret_tuple_as_trans_desc,
                        seed_state=seed)

    if exec_utils.get_param_value(Parameters.PREFIX_CACHE_STATS, parameters, False):
        print(f"@@PREFIX_CACHE@@ hits={_ALIGN_TRIE.hits} inserts={_ALIGN_TRIE.inserts} nodes={_ALIGN_TRIE.nodes}")
    return result


# ========================
# Dijkstra mit Cache
# ========================
def __dijkstra(model_struct, trace_struct, sync_cost, max_align_time_trace=sys.maxsize,
               ret_tuple_as_trans_desc=False, seed_state=None):
    start_time = time.time()
    trans_pre_dict = model_struct[TRANS_PRE_DICT]
    trans_post_dict = model_struct[TRANS_POST_DICT]
    trans_labels_dict = model_struct[TRANS_LABELS_DICT]
    transf_model_cost_function = model_struct[TRANSF_MODEL_COST_FUNCTION]
    transf_trace = trace_struct[TRANSF_TRACE]
    trace_cost_function = trace_struct[TRACE_COST_FUNCTION]
    marking_dict = {}
    im = __encode_marking(marking_dict, model_struct[TRANSF_IM])
    fm = __encode_marking(marking_dict, model_struct[TRANSF_FM])

    if seed_state is not None:
        seed_cost, seed_consumed, seed_marking = seed_state
        initial_state = (seed_cost, -seed_consumed, IS_SYNC_MOVE, 0, 0, None, seed_marking, None)
    else:
        initial_state = (0, 0, 0, 0, 0, None, im, None)

    open_set = [initial_state]
    heapq.heapify(open_set)
    closed = {}
    visited = 0

    while open_set:
        if (time.time() - start_time) > max_align_time_trace:
            return None
        curr = heapq.heappop(open_set)
        curr_m0 = curr[POSITION_MARKING]
        curr_m = __decode_marking(curr_m0)
        if __check_closed(closed, (curr_m0, curr[POSITION_INDEX])):
            continue
        visited += 1

        # --- Prefix-Cache befüllen ---
        try:
            if _PREFIX_CACHE_ENABLED:
                consumed = -curr[POSITION_INDEX]
                if consumed >= 0:
                    _ALIGN_TRIE.insert_state(
                        transf_trace[:consumed], curr_m0, curr[POSITION_TOTAL_COST], consumed
                    )
        except NameError:
            pass

        __add_closed(closed, (curr_m0, curr[POSITION_INDEX]))
        if curr_m0 == fm and -curr[POSITION_INDEX] == len(transf_trace):
            return __reconstruct_alignment(curr, model_struct, trace_struct, visited,
                                           len(open_set), len(closed), len(marking_dict),
                                           ret_tuple_as_trans_desc=ret_tuple_as_trans_desc)

        en_t = [t for t in trans_pre_dict if __dict_leq(trans_pre_dict[t], curr_m)]
        this_closed = set()
        for t in sorted(en_t, key=lambda t: transf_model_cost_function[t]):
            is_sync = (
                -curr[POSITION_INDEX] < len(transf_trace)
                and trans_labels_dict[t] == transf_trace[-curr[POSITION_INDEX]]
            )
            new_m = __encode_marking(marking_dict, __fire_trans(curr_m, trans_pre_dict[t], trans_post_dict[t]))
            new_cost = curr[POSITION_TOTAL_COST] + (sync_cost if is_sync else transf_model_cost_function[t])
            new_state = (
                new_cost,
                curr[POSITION_INDEX] - 1 if is_sync else curr[POSITION_INDEX],
                IS_SYNC_MOVE if is_sync else IS_MODEL_MOVE,
                curr[POSITION_ALIGN_LENGTH] + 1,
                visited,
                curr,
                new_m,
                t,
            )
            if new_m not in this_closed and not __check_closed(closed, (new_m, new_state[POSITION_INDEX])):
                open_set = __add_to_open_set(open_set, new_state)
                this_closed.add(new_m)

        if -curr[POSITION_INDEX] < len(transf_trace) and curr[POSITION_TYPE_MOVE] != IS_MODEL_MOVE:
            new_state = (
                curr[POSITION_TOTAL_COST] + trace_cost_function[-curr[POSITION_INDEX]],
                curr[POSITION_INDEX] - 1,
                IS_LOG_MOVE,
                curr[POSITION_ALIGN_LENGTH] + 1,
                visited,
                curr,
                curr_m0,
                None,
            )
            if not __check_closed(closed, (curr_m0, new_state[POSITION_INDEX])):
                open_set = __add_to_open_set(open_set, new_state)


# ========================
# Hilfsfunktionen
# ========================
def __dict_leq(d1, d2):
    for k in d1:
        if k not in d2:
            return False
        if d1[k] > d2[k]:
            return False
    return True


def __fire_trans(m, preset, postset):
    ret = {}
    for k in m:
        diff = m[k] - preset.get(k, 0)
        if diff > 0:
            ret[k] = diff
    for k, w in postset.items():
        ret[k] = ret.get(k, 0) + w
    return ret


def __encode_marking(marking_dict, m_d):
    keys = sorted(list(m_d.keys()))
    m_t = tuple(k for el in keys for k in [el] * m_d[el])
    if m_t not in marking_dict:
        marking_dict[m_t] = m_t
    return marking_dict[m_t]


def __decode_marking(m_t):
    m_d = {}
    for el in m_t:
        m_d[el] = m_d.get(el, 0) + 1
    return m_d


def __check_closed(closed, ns):
    return ns[0] in closed and closed[ns[0]] <= ns[1]


def __add_closed(closed, ns):
    closed[ns[0]] = ns[1]


def __add_to_open_set(open_set, ns):
    heapq.heappush(open_set, ns)
    return open_set


def __reconstruct_alignment(curr, model_struct, trace_struct,
                            visited, open_set_length, closed_set_length,
                            num_visited_markings, ret_tuple_as_trans_desc=False):
    transf_trace = trace_struct[TRANSF_TRACE]
    inv_labels_dict = trace_struct[INV_TRACE_LABELS_DICT]
    inv_trans_dict = model_struct[INV_TRANS_DICT]
    alignment = []
    cost = curr[POSITION_TOTAL_COST]
    queued = open_set_length + visited
    while curr[POSITION_PARENT_STATE] is not None:
        m_name, m_label, t_name, t_label = ">>", ">>", ">>", ">>"
        if curr[POSITION_TYPE_MOVE] in (IS_SYNC_MOVE, IS_LOG_MOVE):
            name = inv_labels_dict[transf_trace[-curr[POSITION_INDEX] - 1]]
            t_name, t_label = name, name
        if curr[POSITION_TYPE_MOVE] in (IS_SYNC_MOVE, IS_MODEL_MOVE):
            t = inv_trans_dict[curr[POSITION_EN_T]]
            m_name, m_label = t.name, t.label
        if ret_tuple_as_trans_desc:
            alignment.insert(0, ((t_name, m_name), (t_label, m_label)))
        else:
            alignment.insert(0, (t_label, m_label))
        curr = curr[POSITION_PARENT_STATE]
    return {"alignment": alignment, "cost": cost,
            "queued_states": queued, "visited_states": visited,
            "closed_set_length": closed_set_length,
            "num_visited_markings": num_visited_markings}
