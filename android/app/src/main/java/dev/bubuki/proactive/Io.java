package dev.bubuki.proactive;
import java.io.*;
final class Io {
    static byte[] read(InputStream stream,int limit)throws IOException{
        ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] buffer=new byte[8192];int n;
        while((n=stream.read(buffer,0,Math.min(buffer.length,limit-out.size())))>0){out.write(buffer,0,n);if(out.size()>=limit)break;}
        return out.toByteArray();
    }
}
