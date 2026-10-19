using System;
using System.Collections.Generic;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Experimental.Rendering;
using UnityEngine.Rendering;
using UnityEngine.Rendering.RenderGraphModule;
using UnityEngine.Rendering.Universal;

namespace Simkit
{
    // Renders the camera once per capture: URP draws RGB into one target while
    // SimkitIdDepthPass draws instance ids and eye depth into two more.
    class SimkitCapture
    {
        readonly Camera camera;
        readonly SimkitIdDepthPass pass;
        RenderTexture rgb, instances, depth;
        RTHandle instancesHandle, depthHandle;

        public SimkitCapture(Camera camera)
        {
            this.camera = camera;
            var shader = Shader.Find("Hidden/Simkit/IdDepth");
            if (shader == null)
                throw new InvalidOperationException("Hidden/Simkit/IdDepth shader missing from the build");
            pass = new SimkitIdDepthPass(new Material(shader));
            RenderPipelineManager.beginCameraRendering += (_, rendering) =>
            {
                if (rendering == camera)
                    camera.GetUniversalAdditionalCameraData().scriptableRenderer.EnqueuePass(pass);
            };
        }

        public void Resize(int width, int height)
        {
            if (rgb != null && rgb.width == width && rgb.height == height)
                return;
            foreach (var texture in new[] { rgb, instances, depth })
                if (texture != null)
                    texture.Release();
            instancesHandle?.Release();
            depthHandle?.Release();
            rgb = new RenderTexture(width, height, GraphicsFormat.R8G8B8A8_SRGB, GraphicsFormat.D32_SFloat);
            instances = new RenderTexture(width, height, GraphicsFormat.R32_UInt, GraphicsFormat.None);
            depth = new RenderTexture(width, height, GraphicsFormat.R32_SFloat, GraphicsFormat.None);
            instancesHandle = RTHandles.Alloc(instances);
            depthHandle = RTHandles.Alloc(depth);
            pass.SetTargets(instancesHandle, depthHandle);
        }

        public async Awaitable<(Reply, byte[])> Capture()
        {
            var request = new RenderPipeline.StandardRequest { destination = rgb };
            if (!RenderPipeline.SupportsRenderRequest(camera, request))
                throw new InvalidOperationException("the render pipeline does not support StandardRequest");
            RenderPipeline.SubmitRenderRequest(camera, request);

            int w = rgb.width, h = rgb.height;
            var rgbBytes = await Read(rgb);
            var instanceBytes = await Read(instances);
            var depthBytes = await Read(depth);
            var payload = new byte[rgbBytes.Length + instanceBytes.Length + depthBytes.Length];
            rgbBytes.CopyTo(payload, 0);
            instanceBytes.CopyTo(payload, rgbBytes.Length);
            depthBytes.CopyTo(payload, rgbBytes.Length + instanceBytes.Length);
            var reply = new Reply
            {
                flip_y = true,  // Unity textures start at the bottom row
                buffers = new[]
                {
                    new BufferSpec { name = "rgb", dtype = "uint8", shape = new[] { h, w, 4 } },
                    new BufferSpec { name = "instances", dtype = "uint32", shape = new[] { h, w } },
                    new BufferSpec { name = "depth", dtype = "float32", shape = new[] { h, w } },
                },
            };
            return (reply, payload);
        }

        static async Awaitable<byte[]> Read(RenderTexture texture)
        {
            var readback = await AsyncGPUReadback.RequestAsync(texture, 0);
            if (readback.hasError)
                throw new InvalidOperationException($"GPU readback of {texture.graphicsFormat} failed");
            return readback.GetData<byte>().ToArray();
        }
    }

    // Draws every opaque renderer with SimkitIdDepth.shader into two imported
    // targets: its RSUV (the instance id) and its eye depth in meters.
    class SimkitIdDepthPass : ScriptableRenderPass
    {
        static readonly List<ShaderTagId> ShaderTags = new()
        {
            new ShaderTagId("UniversalForward"), new ShaderTagId("UniversalForwardOnly"), new ShaderTagId("SRPDefaultUnlit"),
        };

        readonly Material material;
        RTHandle instances, depth;

        class PassData
        {
            public RendererListHandle renderers;
        }

        public SimkitIdDepthPass(Material material)
        {
            this.material = material;
            renderPassEvent = RenderPassEvent.AfterRenderingOpaques;
        }

        public void SetTargets(RTHandle instances, RTHandle depth)
        {
            this.instances = instances;
            this.depth = depth;
        }

        public override void RecordRenderGraph(RenderGraph renderGraph, ContextContainer frameData)
        {
            var renderingData = frameData.Get<UniversalRenderingData>();
            var cameraData = frameData.Get<UniversalCameraData>();
            var lightData = frameData.Get<UniversalLightData>();

            var drawing = RenderingUtils.CreateDrawingSettings(ShaderTags, renderingData, cameraData, lightData, SortingCriteria.CommonOpaque);
            drawing.overrideMaterial = material;
            drawing.overrideMaterialPassIndex = 0;
            var renderers = renderGraph.CreateRendererList(
                new RendererListParams(renderingData.cullResults, drawing, new FilteringSettings(RenderQueueRange.opaque)));

            var zBuffer = renderGraph.CreateTexture(new TextureDesc(instances.rt.width, instances.rt.height)
            {
                format = GraphicsFormat.D32_SFloat,
                name = "SimkitZ",
            });

            using var builder = renderGraph.AddRasterRenderPass<PassData>("Simkit ids + depth", out var data);
            data.renderers = renderers;
            builder.UseRendererList(renderers);
            builder.SetRenderAttachment(renderGraph.ImportTexture(instances), 0, AccessFlags.Write);
            builder.SetRenderAttachment(renderGraph.ImportTexture(depth), 1, AccessFlags.Write);
            builder.SetRenderAttachmentDepth(zBuffer, AccessFlags.Write);
            builder.AllowPassCulling(false);
            builder.SetRenderFunc((PassData passData, RasterGraphContext context) =>
            {
                context.cmd.ClearRenderTarget(true, true, Color.clear);
                context.cmd.DrawRendererList(passData.renderers);
            });
        }
    }
}
