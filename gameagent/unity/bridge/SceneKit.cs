// Auto-installed by GameAgent. High-level helpers used by AI-generated scene builder scripts.
using System.IO;
using System.Linq;
using Unity.AI.Navigation;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;

namespace GameAgent.Bridge
{
    public static class SceneKit
    {
        public static void EnsureFolder(string folder)
        {
            if (string.IsNullOrEmpty(folder)) return;
            folder = folder.Replace('\\', '/').TrimEnd('/');
            if (AssetDatabase.IsValidFolder(folder)) return;
            var parent = Path.GetDirectoryName(folder)?.Replace('\\', '/');
            EnsureFolder(parent);
            AssetDatabase.CreateFolder(parent, Path.GetFileName(folder));
        }

        public static Scene NewScene(string scenePath)
        {
            EnsureFolder(Path.GetDirectoryName(scenePath));
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            EditorSceneManager.SaveScene(scene, scenePath);
            return scene;
        }

        public static void SaveScene(Scene scene, bool addToBuild = true, int buildIndex = -1)
        {
            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene);
            if (!addToBuild) return;
            var list = EditorBuildSettings.scenes.Where(s => s.path != scene.path).ToList();
            var entry = new EditorBuildSettingsScene(scene.path, true);
            if (buildIndex >= 0 && buildIndex <= list.Count) list.Insert(buildIndex, entry);
            else list.Add(entry);
            EditorBuildSettings.scenes = list.ToArray();
        }

        public static Light CreateSun(Vector3 eulerRotation, Color color, float intensity = 2f)
        {
            var go = new GameObject("Sun");
            go.transform.rotation = Quaternion.Euler(eulerRotation);
            var l = go.AddComponent<Light>();
            l.type = LightType.Directional;
            l.color = color;
            l.intensity = intensity;
            l.shadows = LightShadows.Soft;
            l.shadowStrength = 0.9f;
            RenderSettings.sun = l;
            return l;
        }

        public static void SetupHdriSky(string hdrAssetPath, float exposure = 1f, float rotation = 0f)
        {
            var ti = AssetImporter.GetAtPath(hdrAssetPath) as TextureImporter;
            if (ti != null && ti.textureShape != TextureImporterShape.TextureCube)
            {
                ti.textureShape = TextureImporterShape.TextureCube;
                ti.SaveAndReimport();
            }
            var cube = AssetDatabase.LoadAssetAtPath<Cubemap>(hdrAssetPath);
            if (cube == null) { Debug.LogWarning($"[SceneKit] HDRI cubemap not found at {hdrAssetPath}"); return; }

            EnsureFolder("Assets/Game/Generated");
            var matPath = $"Assets/Game/Generated/Sky_{Path.GetFileNameWithoutExtension(hdrAssetPath)}.mat";
            var mat = AssetDatabase.LoadAssetAtPath<Material>(matPath);
            if (mat == null)
            {
                mat = new Material(Shader.Find("Skybox/Cubemap"));
                AssetDatabase.CreateAsset(mat, matPath);
            }
            mat.SetTexture("_Tex", cube);
            mat.SetFloat("_Exposure", exposure);
            mat.SetFloat("_Rotation", rotation);
            EditorUtility.SetDirty(mat);

            RenderSettings.skybox = mat;
            RenderSettings.ambientMode = AmbientMode.Skybox;
            RenderSettings.defaultReflectionMode = DefaultReflectionMode.Skybox;
            DynamicGI.UpdateEnvironment();
        }

        public static Volume CreateGlobalVolume(string profileAssetPath, float bloom = 0.6f, float exposure = 0f,
            float contrast = 10f, float saturation = 5f, float vignette = 0.2f, bool aces = true)
        {
            EnsureFolder(Path.GetDirectoryName(profileAssetPath));
            if (AssetDatabase.LoadAssetAtPath<VolumeProfile>(profileAssetPath) != null)
                AssetDatabase.DeleteAsset(profileAssetPath);
            var profile = ScriptableObject.CreateInstance<VolumeProfile>();
            AssetDatabase.CreateAsset(profile, profileAssetPath);

            var b = profile.Add<Bloom>(true);
            b.intensity.Override(bloom);
            b.threshold.Override(1f);
            b.scatter.Override(0.7f);
            var t = profile.Add<Tonemapping>(true);
            t.mode.Override(aces ? TonemappingMode.ACES : TonemappingMode.Neutral);
            var c = profile.Add<ColorAdjustments>(true);
            c.postExposure.Override(exposure);
            c.contrast.Override(contrast);
            c.saturation.Override(saturation);
            var v = profile.Add<Vignette>(true);
            v.intensity.Override(vignette);
            v.smoothness.Override(0.4f);

            foreach (var comp in profile.components)
                AssetDatabase.AddObjectToAsset(comp, profile);
            EditorUtility.SetDirty(profile);
            AssetDatabase.SaveAssets();

            var go = new GameObject("Global Volume");
            var vol = go.AddComponent<Volume>();
            vol.isGlobal = true;
            vol.sharedProfile = profile;
            return vol;
        }

        public static Camera CreateMainCamera(Vector3 position, Vector3 euler, bool postProcessing = true)
        {
            var go = new GameObject("Main Camera") { tag = "MainCamera" };
            go.transform.SetPositionAndRotation(position, Quaternion.Euler(euler));
            var cam = go.AddComponent<Camera>();
            cam.nearClipPlane = 0.1f;
            cam.farClipPlane = 1000f;
            go.AddComponent<AudioListener>();
            var data = cam.GetUniversalAdditionalCameraData();
            data.renderPostProcessing = postProcessing;
            data.antialiasing = AntialiasingMode.SubpixelMorphologicalAntiAliasing;
            return cam;
        }

        static Bounds? RendererBounds(GameObject go)
        {
            var rs = go.GetComponentsInChildren<Renderer>();
            if (rs.Length == 0) return null;
            var b = rs[0].bounds;
            foreach (var r in rs) b.Encapsulate(r.bounds);
            return b;
        }

        public static GameObject InstantiateModel(string assetPath, Vector3 position, Quaternion rotation,
            float targetHeight = 0f, Transform parent = null, bool addMeshColliders = false)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            GameObject go;
            if (prefab == null)
            {
                Debug.LogWarning($"[SceneKit] model not found at {assetPath}; using placeholder cube");
                go = GameObject.CreatePrimitive(PrimitiveType.Cube);
                go.name = Path.GetFileNameWithoutExtension(assetPath) + "_Placeholder";
            }
            else
            {
                go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            }
            if (parent != null) go.transform.SetParent(parent, false);
            go.transform.SetPositionAndRotation(position, rotation);

            var b = RendererBounds(go);
            if (targetHeight > 0f && b.HasValue && b.Value.size.y > 1e-4f)
            {
                go.transform.localScale *= targetHeight / b.Value.size.y;
                b = RendererBounds(go);
            }
            if (b.HasValue) go.transform.position += new Vector3(0f, position.y - b.Value.min.y, 0f);

            if (addMeshColliders)
                foreach (var mf in go.GetComponentsInChildren<MeshFilter>())
                    if (mf.sharedMesh != null && mf.GetComponent<Collider>() == null)
                        mf.gameObject.AddComponent<MeshCollider>().sharedMesh = mf.sharedMesh;
            return go;
        }

        public static Material CreateLitMaterial(string assetPath, Color baseColor, float smoothness = 0.5f,
            float metallic = 0f, string baseMapPath = null, string normalMapPath = null, Color? emission = null)
        {
            EnsureFolder(Path.GetDirectoryName(assetPath));
            var mat = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
            if (mat == null)
            {
                mat = new Material(Shader.Find("Universal Render Pipeline/Lit"));
                AssetDatabase.CreateAsset(mat, assetPath);
            }
            mat.SetColor("_BaseColor", baseColor);
            mat.SetFloat("_Smoothness", smoothness);
            mat.SetFloat("_Metallic", metallic);
            if (!string.IsNullOrEmpty(baseMapPath))
                mat.SetTexture("_BaseMap", AssetDatabase.LoadAssetAtPath<Texture2D>(baseMapPath));
            if (!string.IsNullOrEmpty(normalMapPath))
            {
                mat.SetTexture("_BumpMap", AssetDatabase.LoadAssetAtPath<Texture2D>(normalMapPath));
                mat.EnableKeyword("_NORMALMAP");
            }
            if (emission.HasValue)
            {
                mat.SetColor("_EmissionColor", emission.Value);
                mat.EnableKeyword("_EMISSION");
                mat.globalIlluminationFlags = MaterialGlobalIlluminationFlags.RealtimeEmissive;
            }
            EditorUtility.SetDirty(mat);
            return mat;
        }

        public static GameObject SavePrefab(GameObject go, string prefabPath)
        {
            EnsureFolder(Path.GetDirectoryName(prefabPath));
            return PrefabUtility.SaveAsPrefabAsset(go, prefabPath);
        }

        public static void BakeNavMesh(GameObject surfaceRoot)
        {
            var surface = surfaceRoot.GetComponent<NavMeshSurface>();
            if (surface == null) surface = surfaceRoot.AddComponent<NavMeshSurface>();
            surface.collectObjects = CollectObjects.All;
            surface.BuildNavMesh();
            var scene = surfaceRoot.scene;
            if (surface.navMeshData != null && !string.IsNullOrEmpty(scene.path) && !AssetDatabase.Contains(surface.navMeshData))
            {
                var dir = Path.GetDirectoryName(scene.path)?.Replace('\\', '/');
                var path = $"{dir}/{scene.name}_NavMesh.asset";
                if (AssetDatabase.LoadAssetAtPath<Object>(path) != null) AssetDatabase.DeleteAsset(path);
                AssetDatabase.CreateAsset(surface.navMeshData, path);
            }
        }
    }
}

