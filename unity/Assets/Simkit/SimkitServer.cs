using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;

namespace Simkit
{
    // Serves simkit's socket protocol (see source/adapters/remote.py) to one
    // client, then quits. Starts only when the player is launched with -simkitPort.
    public class SimkitServer : MonoBehaviour
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        static void Boot()
        {
            var args = Environment.GetCommandLineArgs();
            int flag = Array.IndexOf(args, "-simkitPort");
            if (flag < 0 || flag + 1 >= args.Length)
                return;
            var host = new GameObject("simkit");
            DontDestroyOnLoad(host);
            _ = host.AddComponent<SimkitServer>().Serve(int.Parse(args[flag + 1]));
        }

        async Awaitable Serve(int port)
        {
            var scene = new SimkitScene();
            var capture = new SimkitCapture(scene.Camera);
            var listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            Debug.Log($"simkit: listening on {port}");
            using (var client = await listener.AcceptTcpClientAsync())
            {
                listener.Stop();
                var stream = client.GetStream();
                while (true)
                {
                    var (header, _) = await ReadMessage(stream);
                    if (header == null)
                        break;
                    var request = JsonUtility.FromJson<Request>(header);
                    Reply reply;
                    byte[] payload = Array.Empty<byte>();
                    try
                    {
                        switch (request.op)
                        {
                            case "apply":
                                await scene.Apply(request.frame);
                                capture.Resize(request.frame.camera.width, request.frame.camera.height);
                                reply = new Reply();
                                break;
                            case "capture":
                                (reply, payload) = await capture.Capture();
                                break;
                            default:
                                throw new ArgumentException($"unknown op '{request.op}'");
                        }
                    }
                    catch (Exception e)
                    {
                        Debug.LogException(e);
                        reply = new Reply { error = e.ToString() };
                    }
                    await WriteMessage(stream, JsonUtility.ToJson(reply), payload);
                }
            }
            Application.Quit();
        }

        static async Task<(string, byte[])> ReadMessage(Stream stream)
        {
            var lengths = new byte[8];
            if (!await ReadExactly(stream, lengths))
                return (null, null);
            var header = new byte[BitConverter.ToUInt32(lengths, 0)];
            var payload = new byte[BitConverter.ToUInt32(lengths, 4)];
            if (!await ReadExactly(stream, header) || !await ReadExactly(stream, payload))
                return (null, null);
            return (Encoding.UTF8.GetString(header), payload);
        }

        static async Task<bool> ReadExactly(Stream stream, byte[] buffer)
        {
            for (int read = 0; read < buffer.Length;)
            {
                int n = await stream.ReadAsync(buffer, read, buffer.Length - read);
                if (n == 0)
                    return false;
                read += n;
            }
            return true;
        }

        static async Task WriteMessage(Stream stream, string header, byte[] payload)
        {
            var body = Encoding.UTF8.GetBytes(header);
            await stream.WriteAsync(BitConverter.GetBytes((uint)body.Length), 0, 4);
            await stream.WriteAsync(BitConverter.GetBytes((uint)payload.Length), 0, 4);
            await stream.WriteAsync(body, 0, body.Length);
            await stream.WriteAsync(payload, 0, payload.Length);
        }
    }

    // Messages. Field names match the JSON from source/adapters/remote.py.
#pragma warning disable 0649
    [Serializable] class Request { public string op; public FrameMessage frame; }
    [Serializable] class FrameMessage { public CameraMessage camera; public ObjectMessage[] objects; public LightMessage[] lights; }
    [Serializable] class CameraMessage { public float[] position; public float[] rotation; public int width; public int height; public float vertical_fov_deg; }
    [Serializable] class ObjectMessage { public float[] position; public float[] rotation; public int id; public string asset; public float scale; }
    [Serializable] class LightMessage { public float[] position; public float[] rotation; public string type; public float intensity; public float color_temperature_k; public float cone_angle_deg; }
    [Serializable] class Reply { public string error = ""; public BufferSpec[] buffers = Array.Empty<BufferSpec>(); public bool flip_y; }
    [Serializable] class BufferSpec { public string name; public string dtype; public int[] shape; }
#pragma warning restore 0649
}
