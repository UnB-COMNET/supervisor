""" CdN-QoE solver — shared logic between deployer and supervisor """

import re
import subprocess

import numpy as np
from ortools.linear_solver import pywraplp

IP_TO_ESTADO_CLIENTE = {
    "192.168.0.2": "SP"
}

IP_TO_ESTADO_SERVIDOR = {
    "192.168.0.1": "ES"
}

ESTADOS = ["ES", "MG", "RJ", "SP"]

# State -> DPID mapping
DEVICE_MAP = {
    "ES": "of:0000000000000001",
    "MG": "of:0000000000000002",
    "RJ": "of:0000000000000003",
    "SP": "of:0000000000000004",
}

RTT_MATRIX = [[0.0 for _ in ESTADOS] for _ in ESTADOS]


# Brief: Normalizes a matrix to [0, 1] using min-max scaling
def normalizar_matriz_min_max(matriz):
    matriz = np.asarray(matriz)
    min_val = np.min(matriz)
    max_val = np.max(matriz)
    if max_val == min_val:
        return np.zeros(matriz.shape)
    return (matriz - min_val) / (max_val - min_val)


# Brief: Normalizes a vector to unit length
def normalizar_vetor(vetor):
    vetor = np.asarray(vetor, dtype=float)
    norma = np.linalg.norm(vetor)
    return vetor / norma if norma != 0 else vetor


# Brief: Builds the weighted adjacency matrix aij = a * nrttm[i][j]
def create_aij(a, nrttm):
    num_nodes = len(ESTADOS)
    aij = [[0.0 for _ in range(num_nodes)] for _ in range(num_nodes)]
    for i in range(num_nodes):
        for j in range(num_nodes):
            aij[i][j] = float(a * nrttm[i][j])
    return aij


# Brief: Reads link-latencies from ONOS via docker exec and populates RTT_MATRIX
# Falls back to a hardcoded topology if the ONOS CLI is unreachable
def get_dynamic_latencies():
    global RTT_MATRIX
    RTT_MATRIX = [[0.0 for _ in ESTADOS] for _ in ESTADOS]

    try:
        cmd_lat = (
            "docker exec -t c1 /root/onos/apache-karaf-4.2.9/bin/client"
            " -u karaf -p karaf 'link-latencies'"
        )
        output_lat = subprocess.check_output(
            cmd_lat, shell=True, stderr=subprocess.STDOUT
        ).decode("utf-8")

        cmd_links = (
            "docker exec -t c1 /root/onos/apache-karaf-4.2.9/bin/client"
            " -u karaf -p karaf 'links'"
        )
        output_links = subprocess.check_output(
            cmd_links, shell=True, stderr=subprocess.STDOUT
        ).decode("utf-8")

        # Build a set of active links to ignore latencies on downed links
        active_links = set()
        for line in output_links.splitlines():
            if "state=ACTIVE" in line:
                m = re.search(r"src=(of:[a-f0-9]+)/\d+, dst=(of:[a-f0-9]+)/\d+", line)
                if m:
                    active_links.add((m.group(1), m.group(2)))

        pattern = r"src=(of:[a-f0-9]+)/\d+, dst=(of:[a-f0-9]+)/\d+.*--- (\d+)ms"
        rev_map = {v: k for k, v in DEVICE_MAP.items()}

        for m in re.finditer(pattern, output_lat):
            src_dpid, dst_dpid = m.group(1), m.group(2)
            if (src_dpid, dst_dpid) not in active_links:
                continue
            src_st = rev_map.get(src_dpid)
            dst_st = rev_map.get(dst_dpid)
            lat = float(m.group(3))
            if src_st and dst_st:
                RTT_MATRIX[ESTADOS.index(src_st)][ESTADOS.index(dst_st)] = lat

    except Exception as e:
        print(f"[CdN-QoE] Failed to read ONOS: {str(e)}")
        print("[CdN-QoE] Injecting fallback topology so the solver does not crash.")

        def add_link(u, v, lat):
            RTT_MATRIX[ESTADOS.index(u)][ESTADOS.index(v)] = lat
            RTT_MATRIX[ESTADOS.index(v)][ESTADOS.index(u)] = lat

        add_link("ES", "MG", 10.0)
        add_link("ES", "RJ", 20.0)
        add_link("MG", "SP", 10.0)
        add_link("RJ", "SP", 10.0)


# Brief: Solves the shortest-path problem with QoE constraints for each target UF
# Returns the source index, best target index, best QoE score, best path edges, and all explored edges
def solve_shortest_path_with_constraints(source_uf: str, target_ufs: list, tx: list):
    get_dynamic_latencies()
    print(f"[CdN-QoE] RTT matrix: {RTT_MATRIX}")

    solver = pywraplp.Solver.CreateSolver("SCIP")
    num_nodes = len(RTT_MATRIX)
    source = ESTADOS.index(source_uf)
    targets = [ESTADOS.index(uf) for uf in target_ufs]

    nrttm = normalizar_matriz_min_max(RTT_MATRIX)
    ntx = normalizar_vetor(tx)
    a, b = 0.75, 0.25

    best_qoe = float("inf")
    best_path, best_target = None, None
    all_edges = set()

    for t in range(len(targets)):
        # Decision variables: x[i,j] = 1 if edge (i,j) is used in the path
        x = {}
        for i in range(num_nodes):
            for j in range(num_nodes):
                if RTT_MATRIX[i][j] > 0:
                    x[i, j] = solver.IntVar(0, 1, f"x_{i}_{j}")

        # Flow conservation constraints
        for v in range(num_nodes):
            if v == source: constraint = solver.Constraint(-1, -1) # flow leaves source
            elif v == targets[t]: constraint = solver.Constraint(1, 1) # flow enters target
            else: constraint = solver.Constraint(0, 0)

            for i in range(num_nodes):
                if (i, v) in x: constraint.SetCoefficient(x[i, v], 1) # incoming flow
            for j in range(num_nodes):
                if (v, j) in x: constraint.SetCoefficient(x[v, j], -1) # outgoing flow

        # Objective: minimize weighted RTT cost minus throughput reward
        aij = create_aij(a, nrttm)
        obj = solver.Objective()
        for (i, j), var in x.items():
            obj.SetCoefficient(var, aij[i][j])
        obj.SetOffset(-b * ntx[t])
        obj.SetMinimization()

        if solver.Solve() == pywraplp.Solver.OPTIMAL:
            qoe = solver.Objective().Value()
            path = [(i, j) for (i, j), var in x.items() if var.solution_value() > 0]
            all_edges.update(path)
            if qoe < best_qoe:
                best_qoe, best_target, best_path = qoe, targets[t], path

        solver.Clear()

    return source, best_target, best_qoe, best_path, list(all_edges)
