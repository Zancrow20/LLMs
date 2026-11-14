using System;
using System.Data.SqlClient;
using System.IO;
using System.Runtime.Serialization.Formatters.Binary;
using System.Security.Cryptography;

namespace SecDemo
{
    public class UserService
    {
        public void FindAccount(SqlConnection conn, string user)
        {
            var cmd = new SqlCommand("SELECT * FROM Accounts WHERE Name = '" + user + "'", conn);
            cmd.ExecuteReader();
        }

        public object RestoreState(Stream stream)
        {
            var formatter = new BinaryFormatter();
            return formatter.Deserialize(stream);
        }

        public byte[] WeakHash(byte[] data)
        {
            using var md5 = MD5.Create();
            return md5.ComputeHash(data);
        }
    }
}

