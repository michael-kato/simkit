using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace Simkit.Editor
{
    // Builds the Linux player that source/adapters/unity.py launches:
    //   Unity -batchmode -quit -projectPath unity -executeMethod Simkit.Editor.SimkitBuild.Linux
    // Creates the project's URP asset, default material and empty scene first if missing.
    public static class SimkitBuild
    {
        const string GeneratedDir = "Assets/Simkit/Generated";
        const string ScenePath = GeneratedDir + "/Simkit.unity";
        const string MaterialPath = GeneratedDir + "/Resources/SimkitDefault.mat";
        // A material in Resources pulls glTFast's shader into the build; textured
        // glTFs may need more variants (turn on Strict Shader Variant Matching to see).
        const string GltfMaterialPath = GeneratedDir + "/Resources/SimkitGltf.mat";
        const string GltfShaderPath = "Packages/com.unity.cloud.gltfast/Runtime/Shader/glTF-pbrMetallicRoughness.shadergraph";
        const string PlayerPath = "Build/simkit.x86_64";

        public static void Linux()
        {
            EnsureProjectAssets();
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = new[] { ScenePath },
                locationPathName = PlayerPath,
                target = BuildTarget.StandaloneLinux64,
            });
            if (report.summary.result != BuildResult.Succeeded)
            {
                Debug.LogError($"simkit: build {report.summary.result} with {report.summary.totalErrors} errors");
                EditorApplication.Exit(1);
            }
        }

        static void EnsureProjectAssets()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(MaterialPath));

            if (GraphicsSettings.defaultRenderPipeline is not UniversalRenderPipelineAsset)
            {
                var renderer = ScriptableObject.CreateInstance<UniversalRendererData>();
                AssetDatabase.CreateAsset(renderer, GeneratedDir + "/SimkitRenderer.asset");
                var pipeline = UniversalRenderPipelineAsset.Create(renderer);
                pipeline.msaaSampleCount = 1;
                AssetDatabase.CreateAsset(pipeline, GeneratedDir + "/SimkitURP.asset");
                GraphicsSettings.defaultRenderPipeline = pipeline;
                for (int level = 0; level < QualitySettings.names.Length; level++)
                {
                    QualitySettings.SetQualityLevel(level, false);
                    QualitySettings.renderPipeline = null;  // use the default pipeline everywhere
                }
            }
            GraphicsSettings.lightsUseLinearIntensity = true;
            GraphicsSettings.lightsUseColorTemperature = true;

            if (AssetDatabase.LoadAssetAtPath<Material>(MaterialPath) == null)
            {
                var material = new Material(Shader.Find("Universal Render Pipeline/Lit")) { color = new Color(0.7f, 0.7f, 0.7f) };
                AssetDatabase.CreateAsset(material, MaterialPath);
            }

            if (AssetDatabase.LoadAssetAtPath<Material>(GltfMaterialPath) == null)
                AssetDatabase.CreateAsset(new Material(AssetDatabase.LoadAssetAtPath<Shader>(GltfShaderPath)), GltfMaterialPath);

            if (!File.Exists(ScenePath))
                EditorSceneManager.SaveScene(EditorSceneManager.NewScene(NewSceneSetup.EmptyScene), ScenePath);

            PlayerSettings.colorSpace = ColorSpace.Linear;
            PlayerSettings.runInBackground = true;
            PlayerSettings.SetGraphicsAPIs(BuildTarget.StandaloneLinux64, new[] { GraphicsDeviceType.Vulkan });
            AssetDatabase.SaveAssets();
        }
    }
}
