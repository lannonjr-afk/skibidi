#ab
# Run Command:
# uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1

import asyncio
import json
import os
import sqlite3
import hashlib
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import anyio

# ==============================================================================
# DATABASE SETUP
# ==============================================================================
def get_db_connection():
    conn = sqlite3.connect("game.db", timeout=20.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_data (
                user_id INTEGER PRIMARY KEY,
                x REAL DEFAULT 200,
                y REAL DEFAULT 0,
                z REAL DEFAULT 0,
                money INTEGER DEFAULT 100,
                inventory TEXT DEFAULT '{"iron": 0, "gold": 0, "silver": 0}',
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB Error] Failed to initialize database: {e}")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

active_connections: list[WebSocket] = []
game_state = {"players": {}}
state_lock = asyncio.Lock()

async def broadcast_loop():
    while True:
        await asyncio.sleep(1 / 30)
        if active_connections:
            message = json.dumps({"type": "state", "gameState": game_state})
            dead = []
            for connection in list(active_connections):
                try:
                    await connection.send_text(message)
                except Exception:
                    dead.append(connection)
            if dead:
                async with state_lock:
                    for conn in dead:
                        if conn in active_connections:
                            active_connections.remove(conn)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(broadcast_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

# CORS Fix: set allow_credentials=False when using wildcard origins ("*")
# to comply with browser security policies
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# FRONTEND CLIENT
# ==============================================================================
HTML_CLIENT = """
<!DOCTYPE html>
<html>
<head>
    <title>Infinite Synced Space Sandbox 3D</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <style>
        body { 
            margin: 0; 
            background: #000; 
            color: #fff; 
            font-family: monospace; 
            overflow: hidden; 
        }
        #ui { 
            position: absolute; 
            top: 15px; 
            left: 15px; 
            background: rgba(10,15,30,0.85); 
            padding: 15px; 
            border: 1px solid #00ffff44; 
            border-radius: 8px; 
            box-shadow: 0 0 15px rgba(0,255,255,0.15);
            pointer-events: none;
            z-index: 10;
        }
        .stat { color: #00ffff; }
        
        #nav-arrow {
            position: absolute;
            width: 0;
            height: 0;
            border-left: 12px solid transparent;
            border-right: 12px solid transparent;
            border-bottom: 24px solid #00ffff;
            filter: drop-shadow(0 0 8px #00ffff);
            pointer-events: none;
            z-index: 20;
            transform-origin: 50% 50%;
            display: none;
        }
        #nav-text {
            position: absolute;
            color: #00ffff;
            font-size: 11px;
            font-weight: bold;
            text-shadow: 0 0 5px #00ffff;
            pointer-events: none;
            z-index: 20;
            white-space: nowrap;
            display: none;
        }
        #toggle-menu-btn {
            position: absolute;
            top: 15px;
            right: 15px;
            background: rgba(10, 15, 30, 0.9);
            color: #00ffff;
            border: 1px solid #00ffff;
            border-radius: 6px;
            padding: 8px 14px;
            font-family: monospace;
            font-weight: bold;
            cursor: pointer;
            z-index: 30;
            box-shadow: 0 0 10px rgba(0, 255, 255, 0.2);
        }
        #toggle-menu-btn:hover { background: rgba(0, 255, 255, 0.2); }
        
        #item-menu {
            position: absolute;
            top: 55px;
            right: 15px;
            background: rgba(10,15,30,0.85); 
            padding: 10px;
            border: 2px solid #ffffff;
            border-radius: 8px;
            display: none;
            z-index: 25;
        }
    </style>
</head>
<body>
    <div id="auth-overlay" style="position: absolute; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(5, 10, 20, 0.85); z-index: 2000; display: flex; justify-content: center; align-items: center; flex-direction: column;">
        <div style="background: rgba(15, 20, 35, 0.95); border: 2px solid #00ffff; padding: 25px; border-radius: 8px; text-align: center; width: 280px;">
            <h2 style="color: #00ffff; margin-top: 0;">PILOT LOGIN</h2>
            <input type="text" id="username" placeholder="Username" style="width: 90%; padding: 8px; margin: 8px 0; background: #111; color: #00ffff; border: 1px solid #00ffff; border-radius: 4px;" />
            <input type="password" id="password" placeholder="Password" style="width: 90%; padding: 8px; margin: 8px 0; background: #111; color: #00ffff; border: 1px solid #00ffff; border-radius: 4px;" />
            <p id="auth-msg" style="color: #ff3344; font-size: 12px; margin: 5px 0;"></p>
            <div style="display: flex; justify-space-around; margin-top: 10px;">
                <button onclick="handleLogin()" style="padding: 8px 15px; background: #00ffff; color: #000; font-weight: bold; border: none; border-radius: 4px; cursor: pointer;">Login</button>
                <button onclick="handleRegister()" style="padding: 8px 15px; background: #00ffff; color: #000; font-weight: bold; border: none; border-radius: 4px; cursor: pointer;">Register</button>
            </div>
        </div>
    </div>

    <div id="trade-menu" style="display: none; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(15, 20, 30, 0.95); border: 2px solid #00d2ff; border-radius: 10px; padding: 20px; color: #fff; font-family: sans-serif; box-shadow: 0 0 20px rgba(0, 210, 255, 0.3); min-width: 320px; z-index: 1000;">
        <h2 style="margin-top: 0; text-align: center; color: #00d2ff; text-transform: uppercase; letter-spacing: 2px;">Station Trading Hub</h2>
        <p style="text-align: center; font-size: 0.9em; color: #aaa; margin-bottom: 20px;">Docked at Station (0, 0, 0)</p>
    
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; background: rgba(255, 255, 255, 0.05); padding: 8px 12px; border-radius: 6px;">
            <span style="font-weight: bold; width: 70px;">Iron</span>
            <div>
                <button onclick="buyResource('iron')" style="background: #28a745; color: white; border: none; padding: 6px 10px; border-radius: 4px; cursor: pointer; margin-right: 5px;">Buy (10$)</button>
                <button onclick="sellResource('iron')" style="background: #dc3545; color: white; border: none; padding: 6px 10px; border-radius: 4px;">Sell (7$)</button>
            </div>
        </div>
    
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; background: rgba(255, 255, 255, 0.05); padding: 8px 12px; border-radius: 6px;">
            <span style="font-weight: bold; width: 70px;">Silver</span>
            <div>
                <button onclick="buyResource('silver')" style="background: #28a745; color: white; border: none; padding: 6px 10px; border-radius: 4px; cursor: pointer; margin-right: 5px;">Buy (25$)</button>
                <button onclick="sellResource('silver')" style="background: #dc3545; color: white; border: none; padding: 6px 10px; border-radius: 4px;">Sell (15$)</button>
            </div>
        </div>
    
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; background: rgba(255, 255, 255, 0.05); padding: 8px 12px; border-radius: 6px;">
            <span style="font-weight: bold; width: 70px;">Gold</span>
            <div>
                <button onclick="buyResource('gold')" style="background: #28a745; color: white; border: none; padding: 6px 10px; border-radius: 4px; cursor: pointer; margin-right: 5px;">Buy (50$)</button>
                <button onclick="sellResource('gold')" style="background: #dc3545; color: white; border: none; padding: 6px 10px; border-radius: 4px;">Sell (35$)</button>
            </div>
        </div>
        <p style="text-align: center; font-size: 0.8em; color: #888;">Press <kbd>T</kbd> or <kbd>ESC</kbd> to exit trade hub</p>
    </div>

    <div id="ui">
        <h3 style="margin-top: 0; color: #00ffff; text-shadow: 0 0 8px #00ffff;">3D Infinite Warp Flight Deck</h3>
        <p>Position: X <span id="pos-x" class="stat">200</span> | Y <span id="pos-y" class="stat">0</span> | Z <span id="pos-z" class="stat">0</span></p>
        <p>Speed: <span id="speed" class="stat">0</span> m/s</p>
        <p>Pilots Online: <span id="player-count" class="stat">0</span></p>
        <p>Active Planets: <span id="planet-count" class="stat">0</span></p>
        <p>Nearest Planet: <span id="nearest-dist" class="stat">N/A</span></p>
        <p>Controls: WASD (Forward/Turn), X/Z (Ascend/Descend)</p>
        <p>Shift to boost | T near station to trade</p>
    </div>

    <button id="toggle-menu-btn" onclick="toggleItemMenu()">🎒 Inventory (I)</button>
    <div id='item-menu'>
        <p> Money: <span id='money' class='stat'>100</span></p>
        <p> Iron: <span id='iron' class='stat'>0</span></p>
        <p> Gold: <span id='gold' class='stat'>0</span></p>
        <p> Silver: <span id='silver' class='stat'>0</span></p>
    </div>
    
    <div id="nav-arrow"></div>
    <div id="nav-text">TARGET</div>

    <script>
        const scene = new THREE.Scene();
        scene.fog = new THREE.FogExp2(0x020208, 0.00002);
        
        const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000000);
        camera.position.set(0, 150, 300);

        const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
        renderer.setSize(window.innerWidth, window.innerHeight);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.25));
        document.body.appendChild(renderer.domElement);

        const ambientLight = new THREE.AmbientLight(0xffffff, 0.8);
        scene.add(ambientLight);

        const sunLight = new THREE.DirectionalLight(0xffffff, 2.5);
        sunLight.position.set(50000, 100000, 50000);
        scene.add(sunLight);

        const originLineGeo = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(0, 0, 0),
            new THREE.Vector3(0, 0, 0)
        ]);
        const originLineMat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.6 });
        const originLine = new THREE.Line(originLineGeo, originLineMat);
        scene.add(originLine);

        const GLOBAL_SEED = 987654321;
        function seededRandom(seed) {
            let x = Math.sin(seed * 9999) * 10000;
            return x - Math.floor(x);
        }

        const planetTextureCache = {};
        function generatePlanetTextures(seed) {
            if (planetTextureCache[seed]) return planetTextureCache[seed];

            const canvas = document.createElement('canvas');
            canvas.width = 512;
            canvas.height = 256;
            const ctx = canvas.getContext('2d');

            let s = seed;
            const rand = () => { s += 1; return seededRandom(s); };

            const globalTemp = rand(); 
            let baseColor, continentColor, isLava = false;

            if (globalTemp > 0.82) {
                baseColor = '#1a0b0b'; continentColor = '#e63900'; isLava = true;
            } else if (globalTemp > 0.62) {
                baseColor = '#8c593b'; continentColor = '#d99b00';
            } else if (globalTemp > 0.38) {
                baseColor = '#0b3d91'; continentColor = '#3a7d44';
            } else if (globalTemp > 0.18) {
                baseColor = '#2b4450'; continentColor = '#607d8b';
            } else {
                baseColor = '#b2ebf2'; continentColor = '#e0f7fa';
            }

            ctx.fillStyle = baseColor;
            ctx.fillRect(0, 0, 512, 256);

            const continentCount = 4 + Math.floor(rand() * 5);
            for (let c = 0; c < continentCount; c++) {
                const cx = rand() * 512;
                const cy = rand() * 256;
                const radiusX = 40 + rand() * 80;
                const radiusY = 30 + rand() * 60;

                ctx.beginPath();
                for (let i = 0; i < 12; i++) {
                    const angle = (i / 12) * Math.PI * 2;
                    const px = cx + Math.cos(angle) * radiusX * (0.6 + rand() * 0.8);
                    const py = cy + Math.sin(angle) * radiusY * (0.6 + rand() * 0.8);
                    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
                }
                ctx.closePath();
                ctx.fillStyle = continentColor;
                ctx.fill();
            }

            const colorTex = new THREE.CanvasTexture(canvas);
            colorTex.wrapS = THREE.RepeatWrapping;
            colorTex.needsUpdate = true;

            const res = { colorTex, isLava };
            planetTextureCache[seed] = res;
            return res;
        }

        const CLUSTER_GRID_SIZE = 150000;
        const CLUSTER_DRAW_RADIUS = 1; 
        const UNLOAD_DISTANCE_THRESHOLD = 350000; 
        const planetObjects = {};
        const sharedSphereGeom = new THREE.SphereGeometry(1, 32, 32);

        function createSystemCluster(cx, cy, cz) {
            const clusterKey = `${cx}_${cy}_${cz}`;
            let seed = GLOBAL_SEED + cx * 73856093 + cy * 19349663 + cz * 83492791;

            const systemCenterX = cx * CLUSTER_GRID_SIZE + (seededRandom(seed) - 0.5) * (CLUSTER_GRID_SIZE * 0.5);
            seed += 1;
            const systemCenterY = cy * CLUSTER_GRID_SIZE + (seededRandom(seed) - 0.5) * (CLUSTER_GRID_SIZE * 0.5);
            seed += 1;
            const systemCenterZ = cz * CLUSTER_GRID_SIZE + (seededRandom(seed) - 0.5) * (CLUSTER_GRID_SIZE * 0.5);

            seed += 1;
            const planetCount = 1 + Math.floor(seededRandom(seed) * 3);

            for (let p = 0; p < planetCount; p++) {
                const pKey = `clusterKeyp{p}`;
                if (planetObjects[pKey] !== undefined) continue;

                seed += 100 + p * 50;
                const angle = seededRandom(seed) * Math.PI * 2;
                
                seed += 103 + p * 50;
                const radius = 6000 + seededRandom(seed) * 9000;

                seed += 101 + p * 50;
                const minOrbit = 45000 + (p * 50000); 
                const clusterOffsetDist = minOrbit + seededRandom(seed) * 20000;

                seed += 102 + p * 50;
                const altOffset = (seededRandom(seed) - 0.5) * 10000;

                const px = systemCenterX + Math.cos(angle) * clusterOffsetDist;
                const py = systemCenterY + Math.sin(angle) * clusterOffsetDist;
                const pz = systemCenterZ + altOffset;

                const textures = generatePlanetTextures(seed);
                const mat = new THREE.MeshStandardMaterial({ 
                    map: textures.colorTex,
                    roughness: textures.isLava ? 0.4 : 0.7,
                    metalness: textures.isLava ? 0.3 : 0.1,
                    emissiveMap: textures.isLava ? textures.colorTex : null,
                    emissive: textures.isLava ? 0xff4400 : 0x000000,
                    emissiveIntensity: textures.isLava ? 0.8 : 0.0
                });

                const mesh = new THREE.Mesh(sharedSphereGeom, mat);
                mesh.scale.set(radius, radius, radius);
                mesh.position.set(px, pz, py);
                scene.add(mesh);

                planetObjects[pKey] = { mesh, x: px, y: py, z: pz, radius, clusterKey, seed };
            }
        }

        function updatePlanetClusters(playerX, playerY, playerZ) {
            const currentChunkX = Math.floor(playerX / CLUSTER_GRID_SIZE);
            const currentChunkY = Math.floor(playerY / CLUSTER_GRID_SIZE);
            const currentChunkZ = Math.floor(playerZ / CLUSTER_GRID_SIZE);

            for (let x = -CLUSTER_DRAW_RADIUS; x <= CLUSTER_DRAW_RADIUS; x++) {
                for (let y = -CLUSTER_DRAW_RADIUS; y <= CLUSTER_DRAW_RADIUS; y++) {
                    for (let z = -CLUSTER_DRAW_RADIUS; z <= CLUSTER_DRAW_RADIUS; z++) {
                        createSystemCluster(currentChunkX + x, currentChunkY + y, currentChunkZ + z);
                    }
                }
            }

            for (let key in planetObjects) {
                const planet = planetObjects[key];
                if (!planet) continue;

                const dx = planet.x - playerX;
                const dy = planet.z - playerY;
                const dz = planet.y - playerZ;
                const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

                if (dist > UNLOAD_DISTANCE_THRESHOLD) {
                    if (planet.mesh) {
                        scene.remove(planet.mesh);
                        planet.mesh.material.dispose();
                    }
                    delete planetObjects[key];
                }
            }

            document.getElementById('planet-count').innerText = Object.keys(planetObjects).length;
        }

        const arrowEl = document.getElementById('nav-arrow');
        const textEl = document.getElementById('nav-text');

        function updatePlanetPointer(playerX, playerY, playerZ) {
            let nearestPlanet = null;
            let minDistance = Infinity;

            for (let key in planetObjects) {
                const planet = planetObjects[key];
                if (!planet) continue;

                const dx = planet.x - playerX;
                const dy = planet.z - playerY;  
                const dz = planet.y - playerZ;
                const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) - planet.radius;

                if (dist < minDistance) {
                    minDistance = dist;
                    nearestPlanet = planet;
                }
            }

            if (!nearestPlanet) {
                arrowEl.style.display = 'none';
                textEl.style.display = 'none';
                document.getElementById('nearest-dist').innerText = 'None in range';
                return;
            }

            const formattedDist = Math.max(0, Math.round(minDistance));
            document.getElementById('nearest-dist').innerText = `${formattedDist} m`;

            const targetPos = new THREE.Vector3(nearestPlanet.x, nearestPlanet.z, nearestPlanet.y);
            const screenPos = targetPos.clone().project(camera);

            const widthHalf = window.innerWidth / 2;
            const heightHalf = window.innerHeight / 2;

            let screenX = (screenPos.x * widthHalf) + widthHalf;
            let screenY = -(screenPos.y * heightHalf) + heightHalf;
            const isBehind = screenPos.z > 1;

            const margin = 50;
            let edgeX = screenX;
            let edgeY = screenY;

            if (isBehind || screenX < margin || screenX > window.innerWidth - margin || screenY < margin || screenY > window.innerHeight - margin) {
                if (isBehind) {
                    edgeX = window.innerWidth - screenX;
                    edgeY = window.innerHeight - screenY;
                }
                const dx = edgeX - widthHalf;
                const dy = edgeY - heightHalf;
                const angle = Math.atan2(dy, dx);

                const maxX = widthHalf - margin;
                const maxY = heightHalf - margin;

                const scaleX = maxX / Math.abs(Math.cos(angle));
                const scaleY = maxY / Math.abs(Math.sin(angle));
                const scale = Math.min(scaleX, scaleY);

                edgeX = widthHalf + Math.cos(angle) * scale;
                edgeY = heightHalf + Math.sin(angle) * scale;
            }

            arrowEl.style.display = 'block';
            arrowEl.style.left = `${edgeX - 12}px`;
            arrowEl.style.top = `${edgeY - 12}px`;

            textEl.style.display = 'block';
            textEl.style.left = `${edgeX - 25}px`;
            textEl.style.top = `${edgeY + 18}px`;
            textEl.innerText = `${formattedDist}m`;
        }

        function createShipMesh(isLocal) {
            const group = new THREE.Group();

            const hullGeo = new THREE.ConeGeometry(8, 24, 4);
            hullGeo.rotateX(-Math.PI / 2);
            const hullMat = new THREE.MeshStandardMaterial({ 
                color: isLocal ? 0x00ff88 : 0xff3344, 
                roughness: 0.3, 
                metalness: 0.8 
            });
            const hull = new THREE.Mesh(hullGeo, hullMat);
            group.add(hull);

            const engineGeo = new THREE.CylinderGeometry(2.5, 0, 14, 6);
            engineGeo.rotateX(-Math.PI / 2);
            const engineMat = new THREE.MeshStandardMaterial({ 
                color: 0xff5500,
                emissive: 0xff4400,
                emissiveIntensity: 2.0
            });
            const engine = new THREE.Mesh(engineGeo, engineMat);
            engine.position.z = 12;
            group.add(engine);

            return group;
        }

        function createStationMesh() {
            const group = new THREE.Group();
            const ringGeo = new THREE.TorusGeometry(80, 6, 8, 32);
            const ringMat = new THREE.MeshStandardMaterial({ color: 0x00ffff, metalness: 0.9, roughness: 0.2 });
            const ring = new THREE.Mesh(ringGeo, ringMat);
            ring.rotation.x = Math.PI / 2;
            group.add(ring);

            const coreGeo = new THREE.SphereGeometry(25, 16, 16);
            const coreMat = new THREE.MeshStandardMaterial({ color: 0x2244aa, metalness: 0.5 });
            const core = new THREE.Mesh(coreGeo, coreMat);
            group.add(core);

            return group;
        }

        const stationMesh = createStationMesh();
        scene.add(stationMesh);

        let ws = null;
        let localPlayerId = null;
        let gameState = { players: {} };
        const shipMeshes = {};
        const keys = {};
        let inventory = { 'iron': 0, 'gold': 0, 'silver': 0, 'money': 100 };
        let lastSyncTime = 0;
        let currentUser = null;

        window.addEventListener('keydown', e => { keys[e.key] = true; });
        window.addEventListener('keyup', e => { keys[e.key] = false; });
        window.addEventListener('resize', () => {
            camera.aspect = window.innerWidth / window.innerHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(window.innerWidth, window.innerHeight);
        });

        function setupWebSocket() {
            const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `wsProtocol//{window.location.host}/ws`;
            ws = new WebSocket(wsUrl);

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'init') {
                    localPlayerId = data.id;

                    if (!gameState.players[localPlayerId]) {
                        gameState.players[localPlayerId] = { x: 200, y: 0, z: 0, angle: 0, vx: 0, vy: 0, vz: 0 };
                    }

                    if (currentUser && currentUser.saveData && currentUser.saveData.position) {
                        const pos = currentUser.saveData.position;
                        gameState.players[localPlayerId].x = Number.isFinite(pos.x) ? pos.x : 200;
                        gameState.players[localPlayerId].y = Number.isFinite(pos.y) ? pos.y : 0;
                        gameState.players[localPlayerId].z = Number.isFinite(pos.z) ? pos.z : 0;
                    }
                    return;
                }
                if (data.type === 'state') {
                    const totalPlayers = Object.keys(data.gameState.players).length;
                    document.getElementById('player-count').innerText = totalPlayers;

                    if (!localPlayerId) return;

                    for (let id in data.gameState.players) {
                        if (id !== localPlayerId) {
                            gameState.players[id] = data.gameState.players[id];
                        }
                    }
                    for (let id in gameState.players) {
                        if (!data.gameState.players[id] && id !== localPlayerId) {
                            delete gameState.players[id];
                            if (shipMeshes[id]) {
                                scene.remove(shipMeshes[id]);
                                delete shipMeshes[id];
                            }
                        }
                    }
                }
            };

            ws.onclose = () => { setTimeout(setupWebSocket, 1000); };
            ws.onerror = (err) => {};
        }

        setupWebSocket();

        function updateUI() {
            document.getElementById('money').textContent = inventory.money || 0;
            document.getElementById('iron').textContent = inventory.iron || 0;
            document.getElementById('gold').textContent = inventory.gold || 0;
            document.getElementById('silver').textContent = inventory.silver || 0;
        }

        function toggleItemMenu() {
            const menu = document.getElementById('item-menu');
            menu.style.display = (menu.style.display === 'none' || menu.style.display === '') ? 'block' : 'none';
        }

        window.addEventListener('keydown', e => {
            if ((e.key === 'i' || e.key === 'I') && !e.repeat) toggleItemMenu();
        });

        const tradePrices = { iron: { buy: 10, sell: 7 }, gold: { buy: 50, sell: 35 }, silver: { buy: 25, sell: 15 } };
        let isTrading = false;
        const TRADE_DISTANCE = 100; 
        
        function toggleTradeMenu() {
            const tradeMenu = document.getElementById('trade-menu');
            const me = gameState.players[localPlayerId];
            if (!me) return;
        
            const distance = Math.sqrt((me.x || 0) ** 2 + (me.y || 0) ** 2 + (me.z || 0) ** 2);
            if (!isTrading && distance <= TRADE_DISTANCE) {
                isTrading = true;
                tradeMenu.style.display = 'block';
            } else {
                isTrading = false;
                tradeMenu.style.display = 'none';
            }
        }
        
        window.addEventListener('keydown', (e) => {
            if (e.key.toLowerCase() === 't') toggleTradeMenu();
            if (e.key === 'Escape' && isTrading) {
                isTrading = false;
                document.getElementById('trade-menu').style.display = 'none';
            }
        });

        async function buyResource(res) {
            if (!localPlayerId || !currentUser) return;
            try {
                const res2 = await fetch('/api/trade', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ userId: currentUser.userId, action: 'buy', resource: res })
                });
                const data = await res2.json();
                if (data.error) { alert(data.error); return; }
                inventory = data.inventory;
                inventory.money = data.money;
                updateUI();
            } catch (err) {}
        }
        
        async function sellResource(res) {
            if (!localPlayerId || !currentUser) return;
            try {
                const res2 = await fetch('/api/trade', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ userId: currentUser.userId, action: 'sell', resource: res })
                });
                const data = await res2.json();
                if (data.error) { alert(data.error); return; }
                inventory = data.inventory;
                inventory.money = data.money;
                updateUI();
            } catch (err) {}
        }

        function updateLocalPhysics() {
            if (localPlayerId && gameState.players[localPlayerId]) {
                const me = gameState.players[localPlayerId];
                
                if (!Number.isFinite(me.x)) me.x = 200;
                if (!Number.isFinite(me.y)) me.y = 0;
                if (!Number.isFinite(me.z)) me.z = 0;
                if (!Number.isFinite(me.angle)) me.angle = 0;
                if (!Number.isFinite(me.vx)) me.vx = 0;
                if (!Number.isFinite(me.vy)) me.vy = 0;
                if (!Number.isFinite(me.vz)) me.vz = 0;
                
                const isThrusting = keys['ArrowUp'] || keys['w'] || keys['W'];
                const isBoosting = keys['Shift'];
                
                if (keys['ArrowLeft'] || keys['a'] || keys['A']) me.angle -= 0.03;
                if (keys['ArrowRight'] || keys['d'] || keys['D']) me.angle += 0.03;
                if (keys['x'] || keys['X']) me.vz += 0.85;
                if (keys['z'] || keys['Z']) me.vz -= 0.85;
                
                if (keys['ArrowDown'] || keys['s'] || keys['S']) {
                    me.vx *= 0.95; me.vy *= 0.95; me.vz *= 0.90;
                }
        
                const maxSpeed = isBoosting ? 100 : 50;
                let currentSpeed = Math.sqrt(me.vx * me.vx + me.vy * me.vy + me.vz * me.vz);
        
                if (isThrusting) {
                    const baseAccel = isBoosting ? 1.0 : 0.4;
                    me.vx += Math.sin(me.angle) * baseAccel;
                    me.vy -= Math.cos(me.angle) * baseAccel;
                }
        
                currentSpeed = Math.sqrt(me.vx * me.vx + me.vy * me.vy + me.vz * me.vz);
                if (currentSpeed > maxSpeed) {
                    const scale = maxSpeed / currentSpeed;
                    me.vx *= scale; me.vy *= scale; me.vz *= scale;
                } else if (!isThrusting) {
                    me.vx *= 0.987; me.vy *= 0.987; me.vz *= 0.950;
                }
        
                me.x += me.vx; 
                me.y += me.vy; 
                me.z += me.vz;
        
                const SHIP_PADDING = 20; 
                for (let key in planetObjects) {
                    const planet = planetObjects[key];
                    if (!planet) continue;
        
                    const dx = me.x - planet.x;
                    const dy = me.y - planet.y;
                    const dz = (me.z || 0) - planet.z;
                    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
                    const minDist = planet.radius + SHIP_PADDING;
        
                    if (dist < minDist && dist > 0) {
                        const nx = dx / dist;
                        const ny = dy / dist;
                        const nz = dz / dist;

                        me.x = planet.x + nx * minDist;
                        me.y = planet.y + ny * minDist;
                        me.z = planet.z + nz * minDist;
        
                        const dot = me.vx * nx + me.vy * ny + me.vz * nz;
                        if (dot < 0) {
                            me.vx -= 1.4 * dot * nx;
                            me.vy -= 1.4 * dot * ny;
                            me.vz -= 1.4 * dot * nz;
                            
                            me.vx *= 0.7; me.vy *= 0.7; me.vz *= 0.7;
                        }
                    }
                }
        
                const now = Date.now();
                if (ws && ws.readyState === WebSocket.OPEN && (now - lastSyncTime > 30)) {
                    lastSyncTime = now;
                    ws.send(JSON.stringify({ 
                        type: 'sync', x: me.x, y: me.y, z: me.z, angle: me.angle, vx: me.vx, vy: me.vy, vz: me.vz 
                    }));
                }
            }
        }

        function updateCameraPosition(me) {
            const cameraDistance = 140; 
            const cameraHeight = 50;    

            const targetCamX = me.x - Math.sin(me.angle) * cameraDistance;
            const targetCamZ = me.y + Math.cos(me.angle) * cameraDistance;
            const targetCamY = me.z + cameraHeight;

            camera.position.x += (targetCamX - camera.position.x) * 0.1;
            camera.position.z += (targetCamZ - camera.position.z) * 0.1;
            camera.position.y += (targetCamY - camera.position.y) * 0.1;

            const lookTarget = new THREE.Vector3(
                me.x + Math.sin(me.angle) * 40,
                me.z,
                me.y - Math.cos(me.angle) * 40
            );
            camera.lookAt(lookTarget);
        }

        function animate() {
            requestAnimationFrame(animate);
            updateLocalPhysics();

            stationMesh.rotation.y += 0.005;

            for (let id in gameState.players) {
                const p = gameState.players[id];
                if (!p) continue;

                if (!shipMeshes[id]) {
                    shipMeshes[id] = createShipMesh(id === localPlayerId);
                    scene.add(shipMeshes[id]);
                }

                const posX = Number.isFinite(p.x) ? p.x : 200;
                const posY = Number.isFinite(p.y) ? p.y : 0;
                const posZ = Number.isFinite(p.z) ? p.z : 0;
                const angle = Number.isFinite(p.angle) ? p.angle : 0;

                if (id === localPlayerId) {
                    shipMeshes[id].position.x = posX;
                    shipMeshes[id].position.y = posZ;
                    shipMeshes[id].position.z = posY;
                    shipMeshes[id].rotation.y = -angle;
                } else {
                    shipMeshes[id].position.x += (posX - shipMeshes[id].position.x) * 0.25;
                    shipMeshes[id].position.y += (posZ - shipMeshes[id].position.y) * 0.25;
                    shipMeshes[id].position.z += (posY - shipMeshes[id].position.z) * 0.25;
                    shipMeshes[id].rotation.y += (-angle - shipMeshes[id].rotation.y) * 0.25;
                }
            }

            const me = gameState.players[localPlayerId];
            if (me) {
                const linePositions = originLine.geometry.attributes.position.array;
                linePositions[0] = me.x;
                linePositions[1] = me.z || 0;
                linePositions[2] = me.y;
                linePositions[3] = 0;
                linePositions[4] = 0;
                linePositions[5] = 0;
                originLine.geometry.attributes.position.needsUpdate = true;

                updateCameraPosition(me);
                updatePlanetClusters(me.x, me.z || 0, me.y);
                updatePlanetPointer(me.x, me.z || 0, me.y);

                const spd = Math.sqrt(me.vx * me.vx + me.vy * me.vy + (me.vz || 0) * (me.vz || 0)).toFixed(1);
                document.getElementById('pos-x').innerText = Math.round(me.x);
                document.getElementById('pos-z').innerText = Math.round(me.y);
                document.getElementById('pos-y').innerText = Math.round(me.z || 0);
                document.getElementById('speed').innerText = spd;
            } else {
                updatePlanetClusters(0, 0, 0);
            }

            renderer.render(scene, camera);
        }

        async function handleRegister() {
            const u = document.getElementById('username').value;
            const p = document.getElementById('password').value;
            if (!u || !p) {
                document.getElementById('auth-msg').innerText = "Username & password required";
                return;
            }
            try {
                const res = await fetch('/api/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: u, password: p })
                });
                const data = await res.json();
                document.getElementById('auth-msg').innerText = data.error ? data.error : "Registered! Click Login.";
            } catch (err) {
                document.getElementById('auth-msg').innerText = "Server connection error";
            }
        }

        async function handleLogin() {
            const u = document.getElementById('username').value;
            const p = document.getElementById('password').value;
            if (!u || !p) {
                document.getElementById('auth-msg').innerText = "Username & password required";
                return;
            }
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: u, password: p })
                });
                const data = await res.json();
                
                if (data.error) {
                    document.getElementById('auth-msg').innerText = data.error;
                } else {
                    currentUser = data;
                    document.getElementById('auth-overlay').style.display = 'none';

                    if (localPlayerId) {
                        if (!gameState.players[localPlayerId]) {
                            gameState.players[localPlayerId] = { angle: 0, vx: 0, vy: 0, vz: 0 };
                        }
                        const pos = data.saveData.position || {};
                        gameState.players[localPlayerId].x = Number.isFinite(pos.x) ? pos.x : 200;
                        gameState.players[localPlayerId].y = Number.isFinite(pos.y) ? pos.y : 0;
                        gameState.players[localPlayerId].z = Number.isFinite(pos.z) ? pos.z : 0;
                        gameState.players[localPlayerId].angle = 0;
                        gameState.players[localPlayerId].vx = 0;
                        gameState.players[localPlayerId].vy = 0;
                        gameState.players[localPlayerId].vz = 0;

                        if (ws && ws.readyState === WebSocket.OPEN) {
                            ws.send(JSON.stringify({
                                type: 'sync',
                                x: gameState.players[localPlayerId].x,
                                y: gameState.players[localPlayerId].y,
                                z: gameState.players[localPlayerId].z,
                                angle: 0, vx: 0, vy: 0, vz: 0
                            }));
                        }
                    }

                    inventory = data.saveData.inventory || { iron: 0, gold: 0, silver: 0 };
                    inventory.money = data.saveData.money ?? 100;
                    updateUI();
                }
            } catch (err) {
                document.getElementById('auth-msg').innerText = "Server connection error";
            }
        }

        async function saveProgress() {
            if (!currentUser || !localPlayerId || !gameState.players[localPlayerId]) return;

            const me = gameState.players[localPlayerId];
            try {
                await fetch('/api/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        userId: currentUser.userId,
                        position: { x: me.x, y: me.y, z: me.z },
                        money: inventory.money,
                        inventory: inventory
                    })
                });
            } catch (err) {}
        }

        setInterval(saveProgress, 10000);
        window.addEventListener('beforeunload', saveProgress);
        
        animate();
    </script>
</body>
</html>
"""

# ==============================================================================
# SERVER ENDPOINTS
# ==============================================================================
class AuthRequest(BaseModel):
    username: str
    password: str

class SaveRequest(BaseModel):
    userId: int
    position: dict
    money: int
    inventory: dict

class TradeRequest(BaseModel):
    userId: int
    action: str
    resource: str

def db_register(username, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        hashed_pass = hash_password(password)
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_pass))
        user_id = cursor.lastrowid

        cursor.execute("INSERT INTO player_data (user_id, x, y, z, money) VALUES (?, 200, 0, 0, 100)", (user_id,))
        conn.commit()
        return {"success": True, "userId": user_id}
    except Exception as e:
        return {"error": f"Registration failed: {str(e)}"}
    finally:
        conn.close()

@app.post("/api/register")
async def register(data: AuthRequest):
    return await anyio.to_thread.run_sync(db_register, data.username, data.password)

def db_login(username, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    hashed_pass = hash_password(password)
    
    cursor.execute("SELECT id, username FROM users WHERE username = ? AND password = ?", (username, hashed_pass))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return {"error": "Invalid username or password"}
    
    user_id, uname = user
    cursor.execute("SELECT x, y, z, money, inventory FROM player_data WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {
            "success": True,
            "userId": user_id,
            "username": uname,
            "saveData": {
                "position": {"x": 200, "y": 0, "z": 0},
                "money": 100,
                "inventory": {"iron": 0, "gold": 0, "silver": 0}
            }
        }

    return {
        "success": True,
        "userId": user_id,
        "username": uname,
        "saveData": {
            "position": {
                "x": row[0] if row[0] is not None else 200,
                "y": row[1] if row[1] is not None else 0,
                "z": row[2] if row[2] is not None else 0
            },
            "money": row[3] if row[3] is not None else 100,
            "inventory": json.loads(row[4]) if row[4] else {"iron": 0, "gold": 0, "silver": 0}
        }
    }

@app.post("/api/login")
async def login(data: AuthRequest):
    return await anyio.to_thread.run_sync(db_login, data.username, data.password)

def db_save(data: SaveRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE player_data 
        SET x = ?, y = ?, z = ?, money = ?, inventory = ? 
        WHERE user_id = ?
    """, (
        data.position.get("x", 200), 
        data.position.get("y", 0), 
        data.position.get("z", 0), 
        data.money, 
        json.dumps(data.inventory), 
        data.userId
    ))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/save")
async def save_game(data: SaveRequest):
    return await anyio.to_thread.run_sync(db_save, data)

def db_trade(data: TradeRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT money, inventory FROM player_data WHERE user_id = ?", (data.userId,))
        row = cursor.fetchone()
        if not row:
            return {"error": "Player data not found"}

        money = row[0]
        inv = json.loads(row[1]) if row[1] else {"iron": 0, "gold": 0, "silver": 0}

        prices = {"iron": {"buy": 10, "sell": 7}, "gold": {"buy": 50, "sell": 35}, "silver": {"buy": 25, "sell": 15}}
        price = prices.get(data.resource)
        if not price:
            return {"error": "Invalid resource"}

        if data.action == "buy":
            if money < price["buy"]:
                return {"error": "Not enough money"}
            money -= price["buy"]
            inv[data.resource] = inv.get(data.resource, 0) + 1
        elif data.action == "sell":
            if inv.get(data.resource, 0) < 1:
                return {"error": "Not enough resources"}
            inv[data.resource] -= 1
            money += price["sell"]
        else:
            return {"error": "Invalid action"}

        cursor.execute("UPDATE player_data SET money = ?, inventory = ? WHERE user_id = ?", (money, json.dumps(inv), data.userId))
        conn.commit()
        return {"success": True, "money": money, "inventory": inv}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()

@app.post("/api/trade")
async def trade(data: TradeRequest):
    return await anyio.to_thread.run_sync(db_trade, data)

@app.get("/")
async def get():
    return HTMLResponse(HTML_CLIENT)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    player_id = str(uuid.uuid4())
    async with state_lock:
        active_connections.append(websocket)
        game_state["players"][player_id] = {"x": 200, "y": 0, "z": 0, "angle": 0, "vx": 0, "vy": 0, "vz": 0}
    
    try:
        await websocket.send_text(json.dumps({"type": "init", "id": player_id}))

        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            if payload.get("type") == "sync":
                async with state_lock:
                    p = game_state["players"].get(player_id)
                if p:
                    x = payload.get("x", p["x"])
                    y = payload.get("y", p["y"])
                    z = payload.get("z", p["z"])
                    if isinstance(x, (int, float)) and abs(x) < 1e9: p["x"] = x
                    if isinstance(y, (int, float)) and abs(y) < 1e9: p["y"] = y
                    if isinstance(z, (int, float)) and abs(z) < 1e9: p["z"] = z
                    p["angle"] = payload.get("angle", p["angle"])
                    p["vx"] = payload.get("vx", p["vx"])
                    p["vy"] = payload.get("vy", p["vy"])
                    p["vz"] = payload.get("vz", p["vz"])
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        async with state_lock:
            if websocket in active_connections:
                active_connections.remove(websocket)
            if player_id in game_state["players"]:
                del game_state["players"][player_id]

