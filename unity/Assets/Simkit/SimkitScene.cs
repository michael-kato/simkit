using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

namespace Simkit
{
    // Builds the scene for one FrameMessage. Poses arrive already in Unity axes.
    class SimkitScene
    {
        public Camera Camera { get; }

        // glTF assets are the Y-up rotation of simkit's Z-up assets (frames.GLTF:
        // gltf = (x, z, -y)). glTFast already mirrors X into Unity's handedness;
        // this turn finishes the mapping onto Unity axes (frames.UNITY).
        static readonly Quaternion GltfToUnity = Quaternion.Euler(0f, 90f, 0f);

        readonly Material defaultMaterial = Resources.Load<Material>("SimkitDefault");
        readonly Dictionary<string, GameObject> templates = new();  // loaded glTF files, inactive
        readonly List<GameObject> spawned = new();

        public SimkitScene()
        {
            Camera = new GameObject("simkit_camera").AddComponent<Camera>();
            Camera.enabled = false;  // rendered on demand by SimkitCapture
            Camera.clearFlags = CameraClearFlags.SolidColor;
            Camera.backgroundColor = new Color(0.6f, 0.6f, 0.6f);
            Camera.nearClipPlane = 0.05f;
            Camera.farClipPlane = 200f;
            RenderSettings.ambientMode = AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.3f, 0.3f, 0.3f);
        }

        public async Awaitable Apply(FrameMessage frame)
        {
            foreach (var old in spawned)
            {
                old.SetActive(false);  // Destroy waits for the end of the frame
                Object.Destroy(old);
            }
            spawned.Clear();

            foreach (var obj in frame.objects)
            {
                var instance = await Instantiate(obj.asset);
                Place(instance.transform, obj.position, obj.rotation);
                instance.transform.localScale = Vector3.one * obj.scale;
                foreach (var renderer in instance.GetComponentsInChildren<MeshRenderer>())
                    renderer.SetShaderUserValue((uint)obj.id);  // read by SimkitIdDepth.shader
                spawned.Add(instance);
            }

            foreach (var message in frame.lights)
            {
                var light = new GameObject($"light_{message.type}").AddComponent<Light>();
                light.type = message.type switch
                {
                    "sun" => LightType.Directional,
                    "point" => LightType.Point,
                    "spot" => LightType.Spot,
                    _ => throw new ArgumentException($"URP has no realtime {message.type} lights"),
                };
                Place(light.transform, message.position, message.rotation);
                light.intensity = message.intensity;
                light.useColorTemperature = true;
                light.colorTemperature = message.color_temperature_k;
                light.color = Color.white;
                light.range = 1000f;
                light.shadows = LightShadows.Soft;
                if (light.type == LightType.Spot)
                    light.spotAngle = message.cone_angle_deg;
                spawned.Add(light.gameObject);
            }

            Place(Camera.transform, frame.camera.position, frame.camera.rotation);
            Camera.fieldOfView = frame.camera.vertical_fov_deg;
        }

        async Awaitable<GameObject> Instantiate(string asset)
        {
            if (asset == "primitive:cube")
            {
                var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                cube.GetComponent<MeshRenderer>().sharedMaterial = defaultMaterial;
                return cube;
            }
            if (!templates.TryGetValue(asset, out var template))
            {
                var gltf = new GLTFast.GltfImport();
                if (!await gltf.Load(asset))
                    throw new ArgumentException($"glTFast could not load {asset}");
                template = new GameObject(asset);
                template.SetActive(false);
                var content = new GameObject("gltf").transform;
                content.SetParent(template.transform, false);
                content.localRotation = GltfToUnity;
                await gltf.InstantiateMainSceneAsync(content);
                templates[asset] = template;
            }
            var instance = Object.Instantiate(template);
            instance.SetActive(true);
            return instance;
        }

        static void Place(Transform transform, float[] position, float[] rotation)
        {
            transform.SetPositionAndRotation(
                new Vector3(position[0], position[1], position[2]),
                new Quaternion(rotation[0], rotation[1], rotation[2], rotation[3]));
        }
    }
}
