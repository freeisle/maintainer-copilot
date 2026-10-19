package dev.maintainer;

import java.io.StringReader;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.xml.sax.InputSource;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;

public class PomTools {
    private static String child(Element element, String name) {
        for (Node n = element.getFirstChild(); n != null; n = n.getNextSibling()) {
            if (n instanceof Element e && name.equals(e.getTagName())) {
                return e.getTextContent().trim();
            }
        }
        return "";
    }

    @Tool(name="inspect_pom", description="Inspect supplied Maven pom.xml text for project coordinates, Java version and declared dependencies. Does not resolve dependencies or read files.")
    public Map<String, Object> inspectPom(@ToolParam(description="Complete pom.xml content, at most 200000 characters") String xml) {
        if (xml == null || xml.isBlank() || xml.length() > 200000) {
            throw new IllegalArgumentException("POM must contain 1..200000 characters");
        }
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
            factory.setExpandEntityReferences(false);
            Element root = factory.newDocumentBuilder().parse(new InputSource(new StringReader(xml))).getDocumentElement();
            if (!"project".equals(root.getTagName())) {
                throw new IllegalArgumentException("Expected project root");
            }
            Map<String,Object> result = new LinkedHashMap<>();
            for (String key : List.of("groupId", "artifactId", "version", "packaging")) {
                result.put(key, child(root,key));
            }
            var properties = root.getElementsByTagName("properties");
            result.put("javaVersion", properties.getLength() == 0 ? "" : child((Element)properties.item(0), "java.version"));
            List<Map<String,String>> dependencies = new ArrayList<>();
            var nodes = root.getElementsByTagName("dependency");
            for (int i = 0; i < nodes.getLength(); i++) {
                Element d = (Element)nodes.item(i);
                dependencies.add(Map.of("groupId",child(d,"groupId"),"artifactId",child(d,"artifactId"),"version",child(d,"version"),"scope",child(d,"scope")));
            }
            result.put("declaredDependencies", dependencies);
            result.put("resolved", false);
            return result;
        } catch (Exception ex) {
            throw new IllegalArgumentException("Invalid or unsafe Maven POM", ex);
        }
    }
}
