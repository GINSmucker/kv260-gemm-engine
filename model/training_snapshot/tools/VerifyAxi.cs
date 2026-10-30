// Independent byte decoder and integer GEMM. No NumPy/ONNX/codec dependency.
using System;
using System.IO;
using System.Collections.Generic;
namespace BRelease {
public static class VerifyAxi {
    static string FileIn(string root,string p) {
        string q=Path.GetFullPath(Path.Combine(root,p));
        if(!q.StartsWith(root+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)) throw new Exception("Escaping path");
        return q;
    }
    static int Signed(byte b) {return b<128 ? b : b-256;}
    static byte[] Blob(string root,string p,int logical) {
        byte[] b=File.ReadAllBytes(FileIn(root,p));
        if(b.Length != ((logical+15)/16)*16) throw new Exception("Length: "+p);
        for(int i=logical;i<b.Length;i++) if(b[i]!=0)throw new Exception("Padding: "+p);
        return b;
    }
    public static int Run(string folder) {
        string root=Path.GetFullPath(folder).TrimEnd(Path.DirectorySeparatorChar);
        string[] lines=File.ReadAllLines(Path.Combine(root,"cases","all_index.tsv")); int calls=0;
        foreach(string line in lines) {
            if(line.StartsWith("case_id\t")||line.Trim().Length==0) continue;
            string[] v=line.Split('\t'); int mode=int.Parse(v[2]), m=int.Parse(v[3]), k=int.Parse(v[4]), n=int.Parse(v[5]);
            if(m<1||k<1||n<1||(long)k*16384>int.MaxValue||mode<0||mode>1||mode==1&&k%4!=0)throw new Exception("Shape/mode");
            byte[] x=Blob(root,v[6],m*k),w=Blob(root,v[7],mode==0?k*n:k/4*n*2),y=Blob(root,v[9],m*n*4);
            byte[] meta=null;
            if(mode==1) {
                int total=k/4*n;meta=Blob(root,v[8],(total+1)/2);
                if(total%2!=0 && (meta[total/2]>>4)!=0)throw new Exception("Unused nibble");
                for(int t=0;t<total;t++){int c=(meta[t/2]>>(4*(t%2)))&15;if((c&3)>=(c>>2))throw new Exception("Position code");}
            }
            // Dense deliberately never opens W_meta: it is an unused interface argument.
            long[] sum=new long[m*n];
            for(int row=0;row<m;row++) {
                if(mode==0) {
                    for(int z=0;z<k;z++){int a=Signed(x[row*k+z]);for(int col=0;col<n;col++)sum[row*n+col]+=(long)a*Signed(w[z*n+col]);}
                } else {
                    for(int g=0;g<k/4;g++)for(int col=0;col<n;col++) {
                        int t=g*n+col,c=(meta[t/2]>>(4*(t%2)))&15;
                        sum[row*n+col]+=(long)Signed(x[row*k+4*g+(c&3)])*Signed(w[2*t])+(long)Signed(x[row*k+4*g+(c>>2)])*Signed(w[2*t+1]);
                    }
                }
            }
            for(int i=0;i<sum.Length;i++) {
                int at=4*i;int expected=unchecked((int)((uint)y[at]|(uint)y[at+1]<<8|(uint)y[at+2]<<16|(uint)y[at+3]<<24));
                if(sum[i]<int.MinValue||sum[i]>int.MaxValue||sum[i]!=expected)throw new Exception("Mismatch: "+v[0]+" mode="+mode+" element="+i);
            }
            calls++;if(calls%25==0)Console.WriteLine("INDEPENDENT_DOTNET "+calls);
        }
        if(calls!=146)throw new Exception("Expected 146 calls");return calls;
    }
}}
