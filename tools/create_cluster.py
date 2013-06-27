#!/usr/bin/env    python
# Copyright 2013 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Script to start up a demo Cassandra cluster on Google Compute Engine."""

import os
import time
import sys

# Read in global config variables
mydir = os.path.dirname(os.path.realpath(__file__))
common = mydir + os.path.sep + "common.py"
execfile(common, globals())


# Find a US region with at least two UP zones.
def find_zones():
    """Find a US region with at least two UP zones."""
    print("=> Finding suitable region, selecting zones:"),
    regions = subprocess.check_output(["gcutil", "--service_version",
            API_VERSION, "--format", "names", "listregions", "--filter",
            "name eq 'us.*'"], stderr=NULL).split('\n')[0:-1]
    for region in regions:
        zones = subprocess.check_output(["gcutil", "--service_version",
                API_VERSION, "--format", "names", "listzones", "--filter",
                "status eq UP", "--filter", "name eq '%s.*'" % region],
                stderr=NULL).split('\n')[0:-1]
        if len(zones) > 1:
            print(zones)
            return zones
    raise BE("Error: No suitable US regions found with 2+ zones")

# Create all nodes synchronously
def create_nodes(zones):
    """Create all nodes synchronously."""
    print("=> Creating %d '%s' '%s' nodes" % (NODES_PER_ZONE*len(zones),
            IMAGE, MACHINE_TYPE))

    img_path = get_image_path()
    if img_path is None:
        raise BE("Error: No matching IMAGE for '%s'" % IMAGE)
    img = "https://www.googleapis.com/compute/%s/%s" % (API_VERSION, img_path)

    for zone in zones:
        for i in range(NODES_PER_ZONE):
            nodename = "%s-%s-%d" % (NODE_PREFIX, zone[-1:], i)

            r = subprocess.call(["gcutil",
                    "--service_version=%s" % API_VERSION,
                    "addinstance", nodename, "--zone=%s" % zone,
                    "--machine_type=%s" % MACHINE_TYPE, "--network=default",
                    "--external_ip_address=ephemeral",
                    "--image=%s" % img, "--persistent_boot_disk=false",
                    "--synchronous_mode"], stdout=NULL, stderr=NULL)
            if r != 0:
                raise BE("Error: could not create node %s" % nodename)
            print("--> Node %s created" % nodename)


# Upload JRE install file to each cluster node
def upload_jre(cluster, jre_path):
    """Upload JRE install file to each cluster node."""
    print("=> Uploading JRE install file to each cluster node:"),
    jre = os.path.basename(jre_path)
    for zone in cluster.keys():
        for node in cluster[zone]:
            # create directory on node
            _ = subprocess.call(["gcutil",
                    "--service_version=%s" % API_VERSION, "ssh",
                    "--zone=%s" % zone, node['name'],
                    "sudo mkdir -p /usr/java/latest"], stdout=NULL, stderr=NULL)
            # push JRE file up
            _ = subprocess.call(["gcutil",
                    "--service_version=%s" % API_VERSION, "push",
                    "--zone=%s" % zone, node['name'], jre_path,
                    "/tmp/%s" % jre], stdout=NULL, stderr=NULL)
            _ = subprocess.call(["gcutil",
                    "--service_version=%s" % API_VERSION, "ssh",
                    "--zone=%s" % zone, node['name'],
                    "sudo cp /tmp/%s /usr/java/latest" % jre],
                    stdout=NULL, stderr=NULL)
            print("."),
            sys.stdout.flush()
    print("done.")


# Customize node_config_tmpl script
def customize_config_script(cluster):
    """Customize the node_config_tmpl script"""
    variable_substitutes = {
        '@JRE6_INSTALL@': JRE6_INSTALL,
        '@JRE6_VERSION@': JRE6_VERSION
    }
    seed_data, seed_ips = _identify_seeds(cluster)
    variable_substitutes['@SEED_IPS@'] = ",".join(seed_ips)
    variable_substitutes['@SNITCH_TEXT@'] = _generate_snitch_text(cluster)
    script_path = _update_node_script(variable_substitutes)
    return seed_data, script_path

# Configure each cluster node
def configure_nodes(cluster, script_path):
    """Configure each cluster node."""
    print("=> Uploading and running configure script on nodes:"),
    for zone in cluster.keys():
        for node in cluster[zone]:
            _ = subprocess.call(["gcutil",
                    "--service_version=%s" % API_VERSION, "push",
                    "--zone=%s" % zone, node['name'], script_path,
                    "/tmp/c.sh"], stdout=NULL, stderr=NULL)
            done = subprocess.call(["gcutil",
                    "--service_version=%s" % API_VERSION, "ssh",
                    "--zone=%s" % zone, node['name'],
                    "sudo chmod +x /tmp/c.sh && sudo /tmp/c.sh"],
                    stdout=NULL, stderr=NULL)
            if done != 0:
                err = "Error: problem uploading/running config script "
                err += "on %s" % node['name']
                raise BE(err)
            print("."),
            sys.stdout.flush()
    print("done.")

# Perform variable substituions on the node_config_tmpl script
def _update_node_script(variable_substitutes):
    """Update the node_config_tmpl script"""
    template = "%s%s%s" % (os.path.dirname(os.path.realpath(__file__)),
            os.path.sep,"node_config_tmpl")
    script_path = template + ".sh"
    template_fh = open(template, "r")
    script_fh = open(script_path, "w")
    for line in template_fh:
        for k, v in variable_substitutes.iteritems():
            if line.find(k) > -1:
                line = line.replace(k,v)
        script_fh.write(line)
    template_fh.close()
    script_fh.close()
    return script_path

# Update the SEED list on each node.
def _identify_seeds(cluster):
    """Update the SEED list on each node."""
    # Select first node from each zone as a SEED node.
    seed_ips = []
    seed_data = []
    for z in cluster.keys():
        seed_node = cluster[z][0]
        seed_ips.append(seed_node['ip'])
        seed_data.append(seed_node)
    return seed_data, seed_ips


# Generate the text for the PropertyFileSnitch file
def _generate_snitch_text(cluster):
    """Generate the text for the PropertyFileSnitch file"""
    i=1
    contents = [
        "# Auto-generated topology snitch during cluster turn-up", "#",
        "# Cassandra node private IP=Datacenter:Rack", "#", ""
    ]
    for z in cluster.keys():
        contents.append("# Zone \"%s\" => ZONE%d" % (z, i))
        for node in cluster[z]:
            contents.append("%s=ZONE%d:RAC1" % (node['ip'], i))
        i+=1
        contents.append("")
    contents.append("# default for unknown hosts")
    contents.append("default=ZONE1:RAC1")
    contents.append("")
    return "\n".join(contents)


# Cleanly start up Cassandra on specified node
def node_start_cassandra(zone, nodename):
    """Cleanly start up Cassandra on specified node"""
    status = "notok"
    tries = 0
    print("--> Attempting to start cassandra on node %s" % nodename),
    while status != "ok" and tries < 5:
        _ = subprocess.call(["gcutil", "--service_version=%s" % API_VERSION,
                "ssh", "--zone=%s" % zone, nodename,
                "sudo service cassandra stop"], stdout=NULL, stderr=NULL)
        _ = subprocess.call(["gcutil", "--service_version=%s" % API_VERSION,
                "ssh", "--zone=%s" % zone, nodename,
                "sudo rm /var/run/cassandra.pid"], stdout=NULL, stderr=NULL)
        _ = subprocess.call(["gcutil", "--service_version=%s" % API_VERSION,
                "ssh", "--zone=%s" % zone, nodename,
                "sudo rm -rf /var/lib/cassandra/*"], stdout=NULL, stderr=NULL)
        _ = subprocess.call(["gcutil", "--service_version=%s" % API_VERSION,
                "ssh", "--zone=%s" % zone,nodename,
                "sudo service cassandra start"], stdout=NULL, stderr=NULL)
        r = subprocess.call(["gcutil", "--service_version=%s" % API_VERSION,
                "ssh", "--zone=%s" % zone,nodename,
                "ls /var/run/cassandra.pid"], stdout=NULL, stderr=NULL)
        if r == 0:
            status = "ok"
            print("UP")
            break
        tries += 1
        print("."),
    if status == "notok":
        print("FAILED")
        raise BE("Error: cassandra failing to start on node %s" % nodename)


# Bring up cassandra on cluster nodes, SEEDs first
def start_cluster(seed_data, cluster):
    """Bring up cassandra on cluster nodes, SEEDs first"""
    # Start SEED nodes first.
    print("=> Starting cassandra cluster SEED nodes")
    started_nodes = []
    for node in seed_data:
        node_start_cassandra(node['zone'], node['name'])
        started_nodes.append(node['name'])

    # Start remaining non-seed nodes.
    print("=> Starting cassandra cluster non-SEED nodes")
    for z in cluster.keys():
        for node in cluster[z]:
            if node['name'] not in started_nodes:
                node_start_cassandra(z, node['name'])


# Display cluster status by running 'nodetool status' on a node
def verify_cluster(cluster):
    """Display cluster status by running 'nodetool status' on a node"""
    keys = cluster.keys()
    zone = keys[0]
    nodename = cluster[zone][0]['name']
    status = subprocess.check_output(["gcutil",
            "--service_version=%s" % API_VERSION, "ssh",
            "--zone=%s" % zone, nodename, "nodetool status"], stderr=NULL)
    print("=> Output from node %s and 'nodetool status'" % nodename)
    print(status)


def main():
    if len(sys.argv) != 2 or not os.path.exists(sys.argv[1]):
        raise BE("Error: Must provide location of Oracle JRE")
    jre_path = sys.argv[1]
    jre_file = os.path.basename(jre_path)
    if jre_file != JRE6_INSTALL:
        err = "Error: JRE version mismatch, expecting "
        err += "'%s' but got '%s'" % (JRE6_INSTALL, jre_file)
        raise BE(err)

    # Find a suitable US region with more than a single UP zone.
    zones = find_zones()
    # Make sure we don't exceed MAX_NODES.
    if NODES_PER_ZONE * len(zones) > MAX_NODES:
        error_string = "Error: MAX_NODES exceeded. Adjust tools/common.py "
        error_string += "NODES_PER_ZONE or MAX_NODES."
        raise BE(error_string)

    # Create the nodes, upload/install JRE, customize/execute config script
    create_nodes(zones)
    cluster = get_cluster()
    upload_jre(cluster, jre_path)
    seed_data, script_path = customize_config_script(cluster)
    configure_nodes(cluster, script_path)

    # Bring up the cluster and give it a minute for nodes to join.
    start_cluster(seed_data, cluster)
    print("=> Cassandra cluster is up and running on all nodes")
    print("=> Sleeping 60 seconds to give nodes time to join cluster")
    time.sleep(60)

    # Run nodetool status on a node and display output.
    verify_cluster(cluster)


if __name__ == '__main__':
    main()
    sys.exit(0)
