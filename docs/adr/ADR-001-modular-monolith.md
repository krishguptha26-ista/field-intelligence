# ADR-001: Modular monolith over microservices
Status: accepted (POC)

One FastAPI service + one React app + one database. Module boundaries
(gateway, connectors, agent, evals) are code boundaries with typed contracts,
ready to extract if scale demands it. Microservices at POC stage would be
architectural theater: more failure modes, zero benefit at this load.
