# Docker, explained like you're five

## The one metaphor

**A Dockerfile is a recipe. An image is the frozen meal. A container is the meal on your plate, being eaten.**

You write the recipe once. Docker cooks it once (that's `build`). Then anyone,
on any Linux machine, can microwave the exact same meal (`run`). Nobody has to
re-buy the ingredients or learn to cook.

That's the whole point of this assignment. MB-System is a nightmare to install.
You install it **once**, inside the recipe, and then it just works everywhere.

---

## The 6 words you need

| Word | Plain English | In our file |
|---|---|---|
| `FROM` | "Start with this pre-made base" | `FROM ubuntu:24.04` — start from a bare Ubuntu |
| `RUN` | "Run this command while cooking" | `RUN apt-get install ...` |
| `COPY` | "Put this file from my laptop into the meal" | `COPY backend/app /app/app` |
| `WORKDIR` | "cd into this folder" | `WORKDIR /app` |
| `EXPOSE` | "This meal talks on port 8000" (documentation) | `EXPOSE 8000` |
| `CMD` | "When someone eats this, do THIS" | `CMD ["uvicorn", ...]` |

---

## Layer caching — the one thing that saves you hours

**Metaphor: a stack of pancakes.**

Every `RUN` / `COPY` line makes one pancake. Docker keeps the stack.
When you rebuild, Docker looks down the stack and asks: *"is this pancake still
the same?"* The moment it finds one that changed, **it throws away that pancake
and every pancake above it** and re-cooks them.

So this order is a disaster:

```dockerfile
COPY backend/app /app/app        # <- changes every time you edit code
RUN cmake --build ... -j2        # <- 40 minutes
```

You change one line of Python → the `COPY` pancake changes → the 40-minute
compile above it gets thrown away → **you recompile MB-System. Every. Single.
Time.**

Our Dockerfile does the opposite:

```dockerfile
RUN git clone ... && cmake --build ...   # 40 min, but NEVER changes
COPY backend/requirements.txt ...        # changes rarely
RUN pip3 install ...                     # 30 sec
COPY backend/app /app/app                # changes constantly - but it's LAST
```

**Rule: slowest and most stable at the top, your own code at the bottom.**

That one rule is why you can edit `pipeline.py` and rebuild in 5 seconds
instead of 40 minutes.

---

## Multi-stage builds (used in the frontend)

**Metaphor: you don't ship the kitchen with the cake.**

```dockerfile
FROM node AS build     # big messy kitchen: compilers, dev tools, 400 MB of node_modules
RUN npm run build      # bake the cake

FROM node AS run       # a clean plate
COPY --from=build /app/.next/standalone ./    # take ONLY the cake
```

The final image has the built app and nothing else. Smaller, and nothing you
don't need is exposed.

---

## docker compose

**Metaphor: a dinner party menu.** One file lists every dish and how they talk
to each other.

We have two dishes:
- `backend` — MB-System + Python API
- `frontend` — Next.js web page

Key thing: **services find each other by their service name.** The frontend
reaches the backend at `http://backend:8000` — not `localhost`, because inside
Docker each container has its own `localhost`. Compose runs a tiny DNS so the
word `backend` resolves to the right container.

```bash
docker compose up --build     # cook everything and serve
docker compose down           # clear the table
docker compose logs -f backend  # watch one dish
```

---

## Volumes

**Metaphor: a container is a whiteboard — wiped when it's thrown away.
A volume is a filing cabinet that stays in the room.**

Our uploads and LAS files go to a volume (`job-data`), not into the container.
Restart the container, the files are still there. And a 2 GB upload doesn't
bloat the container's own storage.

---

## The 5 commands you'll actually type

```bash
docker compose up --build        # build + run everything
docker compose down              # stop everything
docker compose logs -f backend   # tail the backend logs
docker exec -it <container> bash # get a shell INSIDE a running container
docker images                    # what have I built?
```

That last one is gold for debugging: `docker exec -it <id> bash` then type
`mbinfo` and see if it's really there.
