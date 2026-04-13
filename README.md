# lumi-supervisor

Monitors the path deployed by the deployer and requests recalculation when the optimal path changes.

## Behavior

1. The deployer POSTs the calculated path and solver parameters to `/supervise`
2. The supervisor stores the path as the current baseline and starts a 10-second monitor loop
3. Every 10s, the supervisor re-runs the CdN-QoE solver with the same parameters
4. If the new optimal path differs from the current one, the supervisor POSTs `{ recalculate: true }` back to the deployer
5. After a recalculate request, the loop pauses until the deployer sends a new path

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| POST | `/supervise` | Receive calculated path from deployer |

### POST `/supervise` body

```json
{
    "path":       [[0, 2], [2, 3]],
    "source_uf":  "SP",
    "target_ufs": ["ES"],
    "tx":         [500.0]
}
```

## Running

**Local**
```bash
pip install -e .
pip install -r requirements.txt
python -m flask --app app.routes run --port 5151
```

**Docker**
```bash
sudo docker build -t supervisor .
sudo docker run --rm -it --network host -v /var/run/docker.sock:/var/run/docker.sock --name supervisor supervisor
```
